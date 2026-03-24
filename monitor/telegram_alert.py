# monitor/telegram_alert.py — Gửi alert qua Telegram Bot
#
# Dùng Telegram Bot API trực tiếp (httpx), không cần thư viện ngoài.
#
# Gửi alert khi:
#   1. Coin mới được thêm vào watch list (mọi coin pass T0)
#   2. Signal đặc biệt: score ≥ 85, smart money, migration nhanh
#   3. Coin bị degraded hoặc removed
#
# Setup:
#   1. Tạo bot qua @BotFather → lấy token
#   2. Gửi tin nhắn bất kỳ cho bot
#   3. Lấy chat_id: https://api.telegram.org/bot<TOKEN>/getUpdates
#   4. Thêm vào .env:
#      TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
#      TELEGRAM_CHAT_ID=-100123456789

import logging
import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot"


def _enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


async def send_message(text: str, parse_mode: str = "HTML"):
    """Gửi tin nhắn text qua Telegram Bot API."""
    if not _enabled():
        return
    url = f"{_API_BASE}{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning(f"Telegram API error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


# ── Alert Templates ──────────────────────────────────────────────

def _fmoney(v):
    if not v: return "$0"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    if v >= 1e3: return f"${v/1e3:.1f}k"
    return f"${v:.0f}"

def _ftime(ts):
    if not ts: return "—"
    from datetime import datetime, timezone
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return d.strftime("%H:%M %d/%m")

def _fage(ts):
    if not ts: return "—"
    import time
    s = int(time.time()) - ts
    if s < 60: return f"{s}s"
    if s < 3600: return f"{s // 60}m"
    if s < 86400: return f"{s / 3600:.1f}h"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


async def alert_new_coin(symbol: str, mint: str, dex: str, score: int,
                         mcap: float, liq: float, holders: int,
                         pump_created_at: int | None, lp_added_at: int | None,
                         smart_money: list | None, pass_count: int,
                         t0: bool, t1: bool, t2: bool, t3: bool, t4: bool):
    """Alert khi coin mới được thêm vào watch list."""
    if not _enabled():
        return

    if score >= 85: icon = "🟢🚀"
    elif score >= 60: icon = "🟢"
    elif score >= 40: icon = "🟡"
    else: icon = "🔴"

    def _c(v): return "✅" if v else "❌"
    pipeline = f"{_c(t0)}T0 {_c(t1)}T1 {_c(t2)}T2 {_c(t3)}T3 {_c(t4)}T4"

    # Timeline: created → LP → now
    timeline = ""
    if pump_created_at:
        timeline += f"🕐 Coin tạo: <b>{_ftime(pump_created_at)}</b> ({_fage(pump_created_at)} trước)\n"
    if lp_added_at:
        timeline += f"💧 Add LP: <b>{_ftime(lp_added_at)}</b> ({_fage(lp_added_at)} trước)\n"
    if pump_created_at and lp_added_at and lp_added_at > pump_created_at:
        sec = lp_added_at - pump_created_at
        m = sec // 60
        timeline += f"⏱ Pump → DEX: <b>{m}m</b>\n" if sec < 3600 else f"⏱ Pump → DEX: <b>{sec/3600:.1f}h</b>\n"

    # Smart money detail
    sm_text = ""
    if smart_money:
        sm_text = f"💰 <b>Smart Money: {len(smart_money)} wallets</b>\n"
        for w in smart_money[:5]:
            short = w[:6] + "..." + w[-4:] if len(w) > 12 else w
            sm_text += f"   └ <code>{short}</code>\n"

    text = (
        f"{icon} <b>NEW: ${symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 DEX: <b>{dex}</b>\n"
        f"📊 Score: <b>{score}/100</b> ({pass_count}/5 pass)\n"
        f"\n"
        f"💎 MCap: <b>{_fmoney(mcap)}</b>\n"
        f"💧 Liquidity: <b>{_fmoney(liq)}</b>\n"
        f"👥 Holders: <b>{holders}</b>\n"
        f"\n"
        f"{timeline}"
        f"{sm_text}"
        f"🔍 {pipeline}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <code>{mint}</code>\n"
        f"📈 <a href=\"https://dexscreener.com/solana/{mint}\">DexScreener</a>"
        f" · <a href=\"https://birdeye.so/token/{mint}?chain=solana\">Birdeye</a>"
        f" · <a href=\"https://solscan.io/token/{mint}\">Solscan</a>"
    )

    await send_message(text)


async def alert_signal(symbol: str, mint: str, signal_type: str, detail: str, score: int):
    """
    Alert tín hiệu đặc biệt:
      - moon: score ≥ 85 + active
      - smart_money_in: SM vừa mua vào
      - smart_money_out: SM vừa bán
    """
    if not _enabled():
        return

    icons = {
        "moon": "🚀🌕",
        "smart_money_in": "💰🟢",
        "smart_money_out": "💰🔴",
    }
    icon = icons.get(signal_type, "📢")

    text = (
        f"{icon} <b>{signal_type.upper()}: {symbol}</b>\n"
        f"{detail}\n"
        f"Score: {score}/100\n"
        f"🔗 <code>{mint}</code>"
    )
    await send_message(text)


async def alert_status_change(symbol: str, mint: str, old_status: str, new_status: str,
                              reason: str, score: int):
    """Alert khi coin bị degraded hoặc removed."""
    if not _enabled():
        return

    if new_status == "degraded":
        icon = "⚠️"
    elif new_status == "removed":
        icon = "🗑"
    elif new_status == "active":
        icon = "✅"
    else:
        icon = "📢"

    text = (
        f"{icon} <b>{symbol}</b>: {old_status} → <b>{new_status}</b>\n"
        f"{reason}\n"
        f"Score: {score}/100\n"
        f"🔗 <code>{mint}</code>"
    )
    await send_message(text)
