# monitor/telegram_bot.py — Telegram Bot for PumpScan
#
# Commands:
#   /watch <mint_address>   — Thêm coin vào watch list, chạy T0-T4, alert kết quả
#   /unwatch <mint_address> — Xóa coin khỏi watch list
#   /list                   — Xem danh sách coin đang watch
#   /status                 — Tổng quan hệ thống
#
# Dùng long-polling (getUpdates) — không cần webhook, không cần domain.

import asyncio
import json
import logging
import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from database.engine import SessionLocal
from database.models import WatchedCoin

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot"
_last_update_id = 0


def _enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


async def _send(chat_id: str | int, text: str):
    url = f"{_API}{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(url, json={
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")


async def _get_updates(offset: int = 0, timeout: int = 30) -> list[dict]:
    url = f"{_API}{TELEGRAM_BOT_TOKEN}/getUpdates"
    async with httpx.AsyncClient(timeout=timeout + 5) as client:
        try:
            resp = await client.get(url, params={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": '["message"]',
            })
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", [])
        except Exception:
            pass
    return []


def _extract_mint(text: str) -> str | None:
    """Extract Solana mint address from message text."""
    parts = text.strip().split()
    for part in parts:
        clean = part.strip()
        # Solana address: 32-44 base58 chars, no 0x prefix
        if len(clean) >= 32 and len(clean) <= 48 and not clean.startswith("0x"):
            # Basic base58 check
            if all(c in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz" for c in clean):
                return clean
    return None


async def _handle_watch(chat_id: int, text: str):
    mint = _extract_mint(text)
    if not mint:
        await _send(chat_id, "❌ Gửi: <code>/watch &lt;mint_address&gt;</code>\n\nVí dụ:\n<code>/watch DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263</code>")
        return

    await _send(chat_id, f"🔍 Đang check <code>{mint[:8]}...{mint[-4:]}</code> ...")

    from monitor.coin_adder import add_coin
    result = await add_coin(mint)

    if result["ok"]:
        r = result
        from monitor.telegram_alert import _fmoney, _ftime, _fage

        # Timeline
        timeline = ""
        if r.get("pump_created_at"):
            timeline += f"🕐 Coin tạo: <b>{_ftime(r['pump_created_at'])}</b> ({_fage(r['pump_created_at'])} trước)\n"
        if r.get("lp_added_at"):
            timeline += f"💧 Add LP: <b>{_ftime(r['lp_added_at'])}</b> ({_fage(r['lp_added_at'])} trước)\n"
        if r.get("pump_created_at") and r.get("lp_added_at"):
            sec = r["lp_added_at"] - r["pump_created_at"]
            if sec > 0:
                timeline += f"⏱ Pump → DEX: <b>{sec // 60}m</b>\n" if sec < 3600 else f"⏱ Pump → DEX: <b>{sec/3600:.1f}h</b>\n"

        # Smart money
        sm_text = ""
        if r["smart_money"]:
            sm_text = f"💰 Smart Money: <b>{r['smart_money']} wallets</b> (via Birdeye)\n"

        await _send(chat_id,
            f"✅ <b>${r['symbol']}</b> đã thêm vào watch list!\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 DEX: <b>{r['dex']}</b>\n"
            f"📊 Score: <b>{r['score']}/100</b> ({r['pass_count']}/5 pass)\n"
            f"\n"
            f"💎 MCap: <b>{_fmoney(r['mcap'])}</b>\n"
            f"💧 Liquidity: <b>{_fmoney(r['liq'])}</b>\n"
            f"👥 Holders: <b>{r['holders']}</b>\n"
            f"\n"
            f"{timeline}"
            f"{sm_text}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 <code>{mint}</code>\n"
            f"📈 <a href=\"https://dexscreener.com/solana/{mint}\">DexScreener</a>"
            f" · <a href=\"https://birdeye.so/token/{mint}?chain=solana\">Birdeye</a>\n"
            f"\n"
            f"🔄 Auto check mỗi 2 phút | Alert qua Telegram"
        )
    else:
        await _send(chat_id, f"❌ {result['error']}")


async def _handle_unwatch(chat_id: int, text: str):
    mint = _extract_mint(text)
    if not mint:
        await _send(chat_id, "❌ Gửi: <code>/unwatch &lt;mint_address&gt;</code>")
        return

    from monitor.coin_adder import remove_coin
    result = await remove_coin(mint)
    if result["ok"]:
        await _send(chat_id, f"🗑 Đã xóa <b>{result['symbol']}</b> khỏi watch list")
    else:
        await _send(chat_id, f"❌ {result['error']}")


async def _handle_list(chat_id: int):
    db = SessionLocal()
    try:
        coins = db.query(WatchedCoin).order_by(WatchedCoin.score.desc()).all()
        if not coins:
            await _send(chat_id, "📭 Watch list trống.\n\nThêm coin: <code>/watch &lt;mint&gt;</code>")
            return

        active = [c for c in coins if c.status == "active"]
        degraded = [c for c in coins if c.status == "degraded"]
        removed = [c for c in coins if c.status == "removed"]

        lines = [f"📋 <b>Watch List</b> ({len(coins)} coins)\n"]

        if active:
            lines.append(f"<b>🟢 Active ({len(active)})</b>")
            for c in active:
                sm = len(json.loads(c.smart_money)) if c.smart_money else 0
                sm_tag = f" 💰{sm}" if sm else ""
                lines.append(f"  {c.symbol} — {c.score}pt{sm_tag} | {c.dex or '?'}")

        if degraded:
            lines.append(f"\n<b>🟡 Degraded ({len(degraded)})</b>")
            for c in degraded:
                lines.append(f"  {c.symbol} — {c.score}pt | {c.dex or '?'}")

        if removed:
            lines.append(f"\n<b>🔴 Removed ({len(removed)})</b>")
            for c in removed[:5]:
                lines.append(f"  {c.symbol} — {c.score}pt")
            if len(removed) > 5:
                lines.append(f"  ... +{len(removed)-5} more")

        await _send(chat_id, "\n".join(lines))
    finally:
        db.close()


async def _handle_status(chat_id: int):
    db = SessionLocal()
    try:
        coins = db.query(WatchedCoin).all()
        active = sum(1 for c in coins if c.status == "active")
        degraded = sum(1 for c in coins if c.status == "degraded")
        removed = sum(1 for c in coins if c.status == "removed")
        sm = sum(1 for c in coins if c.smart_money and json.loads(c.smart_money))

        await _send(chat_id,
            f"📊 <b>PumpScan Status</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🟢 Active: {active}\n"
            f"🟡 Degraded: {degraded}\n"
            f"🔴 Removed: {removed}\n"
            f"💰 Smart Money: {sm}\n"
            f"📦 Total: {len(coins)}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Health check: mỗi 2 phút"
        )
    finally:
        db.close()


async def _handle_message(update: dict):
    msg = update.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()

    if not chat_id or not text:
        return

    if text.startswith("/watch ") or text.startswith("/w "):
        await _handle_watch(chat_id, text)
    elif text.startswith("/unwatch ") or text.startswith("/uw "):
        await _handle_unwatch(chat_id, text)
    elif text in ("/list", "/l"):
        await _handle_list(chat_id)
    elif text in ("/status", "/s"):
        await _handle_status(chat_id)
    elif text == "/start" or text == "/help":
        await _send(chat_id,
            "🔍 <b>PumpScan — Pump.fun Token Monitor</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Chỉ hỗ trợ token từ <b>pump.fun</b>\n\n"
            "/watch &lt;mint&gt; — Thêm token vào watch list\n"
            "/unwatch &lt;mint&gt; — Xóa token\n"
            "/list — Xem watch list\n"
            "/status — Tổng quan\n\n"
            "Hoặc paste trực tiếp mint address!\n\n"
            "Khi add token, hệ thống sẽ:\n"
            "• Verify pump.fun origin\n"
            "• Check sàn DEX nào (Raydium/Orca/...)\n"
            "• Lấy MCap, Liquidity, Holders\n"
            "• Check Smart Money qua Birdeye\n"
            "• Auto monitor mỗi 2 phút"
        )
    elif _extract_mint(text):
        # User paste trực tiếp address → auto watch
        await _handle_watch(chat_id, f"/watch {text}")


async def start_telegram_bot():
    """Long-polling loop for Telegram bot."""
    if not _enabled():
        logger.info("Telegram bot disabled (TELEGRAM_BOT_TOKEN not set)")
        return

    global _last_update_id
    logger.info("🤖 Telegram bot started (long-polling)")

    while True:
        try:
            updates = await _get_updates(offset=_last_update_id + 1, timeout=30)
            for update in updates:
                _last_update_id = update.get("update_id", _last_update_id)
                await _handle_message(update)
        except Exception as e:
            logger.warning(f"Telegram bot error: {e}")
            await asyncio.sleep(5)
