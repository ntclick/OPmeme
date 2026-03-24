# monitor/coin_adder.py — Add/remove pump.fun token to watch list
#
# CHỈ CHẤP NHẬN TOKEN TỪ PUMP.FUN
#
# Flow:
#   1. Pump.fun API: verify origin + lấy created_timestamp, raydium_pool, creator
#   2. DexScreener: sàn nào, mcap, liq, thời gian add LP
#   3. Solana RPC: mintAuth, freezeAuth
#   4. Birdeye: holders, smart money
#   5. Save DB + Telegram alert

import time
import json
import logging
import httpx

from config import (
    HELIUS_API_KEY, BIRDEYE_API_KEY,
    MIN_MCAP_T2, MIN_LIQ_T2, MAX_TOP10_PCT,
    SCORE_MOON, PASS_COUNT_MIN,
)
from database.engine import SessionLocal
from database.models import WatchedCoin
from database.crud import log_event
from monitor.lp_detector import calculate_score

logger = logging.getLogger(__name__)

PUMPFUN_API = "https://frontend-api-v3.pump.fun"

SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}

DEXSCREENER_DEX_MAP = {
    "raydium": "Raydium", "orca": "Orca", "meteora": "Meteora",
    "fluxbeam": "FluxBeam", "lifinity": "Lifinity",
}


def _fmt_money(v):
    if not v: return "$0"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    if v >= 1e3: return f"${v/1e3:.1f}k"
    return f"${v:.0f}"


# ── Pump.fun API ───────────────────────────────────────────────────

async def _pumpfun_lookup(mint: str) -> dict | None:
    """
    Query pump.fun API for token info.

    Returns dict with:
      - is_pumpfun: bool — token exists on pump.fun
      - complete: bool — graduated (bonding curve complete)
      - raydium_pool: str|None — Raydium pool address
      - created_timestamp: int|None — when created on pump.fun (ms)
      - creator: str|None — creator wallet
      - name, symbol, description
      - image_uri, twitter, telegram, website

    pump.fun tokens that haven't graduated: complete=False, raydium_pool=None
    Non-pump.fun tokens: complete=False, all fields empty
    """
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{PUMPFUN_API}/coins/{mint}")
            if resp.status_code != 200:
                return None
            d = resp.json()

            created_ts = d.get("created_timestamp")
            complete = d.get("complete", False)
            raydium_pool = d.get("raydium_pool")

            # Real pump.fun tokens: complete=True + raydium_pool exists
            # Tokens indexed by pump.fun but not created there: complete=False
            is_pumpfun = complete and bool(raydium_pool)

            return {
                "is_pumpfun": is_pumpfun,
                "complete": d.get("complete", False),
                "raydium_pool": d.get("raydium_pool"),
                "created_timestamp": int(created_ts / 1000) if created_ts and created_ts > 1e12 else created_ts,
                "creator": d.get("creator"),
                "name": d.get("name", ""),
                "symbol": d.get("symbol", ""),
                "description": (d.get("description") or "")[:200],
                "image_uri": d.get("image_uri"),
                "twitter": d.get("twitter"),
                "telegram": d.get("telegram"),
                "website": d.get("website"),
            }
        except Exception as e:
            logger.warning(f"Pump.fun API failed for {mint[:12]}...: {e}")
            return None


# ── Helpers ────────────────────────────────────────────────────────

async def _dexscreener_lookup(mint: str) -> dict | None:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
                if pairs:
                    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
        except Exception:
            pass
    return None


async def _solana_rpc_check(mint: str) -> dict:
    from scrapers.solana_scraper import SolanaScraper
    scraper = SolanaScraper()
    try:
        info = await scraper.get_token_info(mint)
        if not info:
            return {"pass": False, "mint_renounced": False}
        return {
            "pass": info.get("is_mint_renounced", False),
            "mint_renounced": info.get("is_mint_renounced", False),
            "freeze_renounced": info.get("freeze_authority") is None,
        }
    except Exception as e:
        logger.warning(f"RPC check failed: {e}")
        return {"pass": False, "mint_renounced": False}


# ── Main: Add Coin ─────────────────────────────────────────────────

async def add_coin(mint: str) -> dict:
    """
    Add a pump.fun token to watch list.
    Rejects non-pump.fun tokens.
    """
    mint = mint.strip()

    if mint in SKIP_MINTS:
        return {"ok": False, "error": "Stablecoin/wrapped token"}

    if len(mint) < 32 or len(mint) > 48:
        return {"ok": False, "error": "Địa chỉ mint không hợp lệ"}

    db = SessionLocal()
    try:
        existing = db.query(WatchedCoin).filter(WatchedCoin.mint == mint).first()
        if existing:
            return {
                "ok": False,
                "error": f"Đã watch: {existing.symbol} (score {existing.score}, {existing.status})",
            }

        now = int(time.time())

        # ══════════════════════════════════════════════════════════
        # Step 1: PUMP.FUN API — verify + get creation data
        # ══════════════════════════════════════════════════════════
        pf = await _pumpfun_lookup(mint)

        if pf is None:
            return {"ok": False, "error": "Không thể kết nối pump.fun API"}

        if not pf["is_pumpfun"]:
            return {"ok": False, "error": "❌ Token không phải từ pump.fun"}

        if not pf["complete"]:
            return {
                "ok": False,
                "error": "⏳ Token chưa graduate khỏi pump.fun (bonding curve chưa complete)",
            }

        pump_created_at = pf["created_timestamp"]
        pf_symbol = pf["symbol"]
        pf_name = pf["name"]
        pf_creator = pf["creator"]

        t0 = True  # Verified pump.fun + graduated

        # ══════════════════════════════════════════════════════════
        # Step 2: DexScreener — sàn nào, mcap, liq
        # ══════════════════════════════════════════════════════════
        dex_pair = await _dexscreener_lookup(mint)

        symbol = pf_symbol or "???"
        name = pf_name or ""
        mcap = 0
        liq = 0
        dex_name = "Raydium"  # default for pump.fun graduates
        lp_added_at = now

        if dex_pair:
            base = dex_pair.get("baseToken", {})
            if not symbol or symbol == "???":
                symbol = base.get("symbol", symbol)
            if not name:
                name = base.get("name", name)
            mcap = dex_pair.get("fdv", 0) or dex_pair.get("marketCap", 0) or 0
            liq_data = dex_pair.get("liquidity", {})
            liq = liq_data.get("usd", 0) if isinstance(liq_data, dict) else 0

            ds_dex = (dex_pair.get("dexId") or "").lower()
            dex_name = DEXSCREENER_DEX_MAP.get(ds_dex, ds_dex.title() if ds_dex else "Raydium")

            pair_ts = dex_pair.get("pairCreatedAt", 0)
            if pair_ts and pair_ts > 1e12:
                lp_added_at = int(pair_ts / 1000)
            elif pair_ts:
                lp_added_at = int(pair_ts)

        # ══════════════════════════════════════════════════════════
        # Step 3: Solana RPC — mintAuth, freezeAuth
        # ══════════════════════════════════════════════════════════
        t1_result = await _solana_rpc_check(mint)
        t1 = t1_result["pass"]

        # ══════════════════════════════════════════════════════════
        # Step 4: Market metrics (from DexScreener, free)
        # ══════════════════════════════════════════════════════════
        t2 = mcap >= MIN_MCAP_T2 and liq >= MIN_LIQ_T2
        holders = 0

        # ══════════════════════════════════════════════════════════
        # Step 5: Birdeye — holders + smart money
        # ══════════════════════════════════════════════════════════
        smart_money_wallets = []
        t3 = False
        t4 = False
        birdeye_checked = False

        if BIRDEYE_API_KEY:
            try:
                from scrapers.birdeye_client import create_birdeye_client
                be = create_birdeye_client()

                overview = await be.get_token_overview(mint)
                if overview:
                    holders = overview.get("holder", 0) or 0
                    if mcap == 0:
                        mcap = overview.get("mc", 0) or overview.get("marketCap", 0) or 0
                    if liq == 0:
                        liq = overview.get("liquidity", 0) or 0

                sm_data = await be.get_smart_money_stats(mint)
                if sm_data:
                    sm_items = sm_data.get("wallets", []) or sm_data.get("items", [])
                    if isinstance(sm_items, list):
                        smart_money_wallets = [
                            w.get("address", w) if isinstance(w, dict) else str(w)
                            for w in sm_items[:10]
                        ]
                t3 = True

                if t3:
                    security = await be.get_token_security(mint)
                    birdeye_checked = True
                    if security:
                        t4 = security.get("top10HolderPercent", 100) < MAX_TOP10_PCT
                    else:
                        t4 = True
            except Exception as e:
                logger.warning(f"Birdeye failed for {symbol}: {e}")
                t3 = True
        else:
            t3 = True
            t4 = True

        # ══════════════════════════════════════════════════════════
        # Score + Save
        # ══════════════════════════════════════════════════════════
        pass_count = sum([t0, t1, t2, t3, t4])
        score = calculate_score(pass_count, holders, mcap)
        status = "active" if pass_count >= PASS_COUNT_MIN else "degraded"

        migration_sec = None
        if pump_created_at and lp_added_at and lp_added_at > pump_created_at:
            migration_sec = lp_added_at - pump_created_at

        coin = WatchedCoin(
            mint=mint, symbol=symbol, name=name, dex=dex_name,
            status=status, score=score, mcap=mcap, liq=liq, holders=holders,
            smart_money=json.dumps(smart_money_wallets) if smart_money_wallets else None,
            pump_created_at=pump_created_at, lp_added_at=lp_added_at,
            last_checked=now, degraded_at=now if status == "degraded" else None,
            filter_t0=t0, filter_t1=t1, filter_t2=t2, filter_t3=t3, filter_t4=t4,
            birdeye_checked=birdeye_checked,
        )
        db.add(coin)
        db.commit()

        # Events
        mig_text = f" | Pump→DEX: {migration_sec // 60}m" if migration_sec else ""
        log_event(db, mint, symbol, "new",
            f"pump.fun → {dex_name} | MCap {_fmt_money(mcap)} | Liq {_fmt_money(liq)}{mig_text}",
            score=score)
        if status == "active":
            log_event(db, mint, symbol, "ok", f"Pass {pass_count}/5. Score: {score}", score=score)
        if smart_money_wallets:
            for w in smart_money_wallets[:3]:
                short = w[:6] + "..." + w[-4:] if len(w) > 12 else w
                log_event(db, mint, symbol, "sm", f"{short} smart money (Birdeye)", score=score)

        logger.info(f"✅ {symbol} | pump.fun → {dex_name} | score={score} | sm={len(smart_money_wallets)}")

        # Telegram Alert
        from monitor.telegram_alert import alert_new_coin, alert_signal
        await alert_new_coin(
            symbol=symbol, mint=mint, dex=dex_name, score=score,
            mcap=mcap, liq=liq, holders=holders,
            pump_created_at=pump_created_at, lp_added_at=lp_added_at,
            smart_money=smart_money_wallets, pass_count=pass_count,
            t0=t0, t1=t1, t2=t2, t3=t3, t4=t4,
        )
        if score >= SCORE_MOON and status == "active":
            await alert_signal(symbol, mint, "moon", f"Score {score} on {dex_name}", score)

        return {
            "ok": True,
            "symbol": symbol,
            "name": name,
            "dex": dex_name,
            "score": score,
            "status": status,
            "mcap": mcap,
            "liq": liq,
            "holders": holders,
            "pass_count": pass_count,
            "smart_money": len(smart_money_wallets),
            "pump_created_at": pump_created_at,
            "lp_added_at": lp_added_at,
            "migration_sec": migration_sec,
            "creator": pf_creator,
            "twitter": pf.get("twitter"),
            "telegram": pf.get("telegram"),
            "website": pf.get("website"),
        }

    except Exception as e:
        logger.error(f"Error adding coin {mint[:12]}...: {e}")
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def remove_coin(mint: str) -> dict:
    """Remove a coin from watch list."""
    db = SessionLocal()
    try:
        coin = db.query(WatchedCoin).filter(WatchedCoin.mint == mint).first()
        if not coin:
            return {"ok": False, "error": "Coin không có trong watch list"}
        symbol = coin.symbol
        db.delete(coin)
        db.commit()
        logger.info(f"🗑 Removed: {symbol} ({mint[:8]}...)")
        return {"ok": True, "symbol": symbol}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()
