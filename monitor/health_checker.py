# monitor/health_checker.py — Periodic health checker for watched coins
#
# Runs every ~2 minutes:
#   1. Update mcap/liq/holders/score for all active + degraded coins
#   2. Degrade coins that fail T2 thresholds
#   3. Remove coins that have been degraded for >24h
#   4. Check smart money movements
#   5. Log all state changes to event_log

import asyncio
import time
import logging
import httpx

from config import (
    MIN_MCAP_T2, MIN_LIQ_T2, MIN_HOLDERS_T2,
    DEGRADE_REMOVE_H, SCORE_ACTIVE_MIN, SCORE_RECOVER, PASS_COUNT_MIN,
    PENDING_AUTO_PROMOTE_LIQ, PENDING_MAX_AGE_H,
)
from database.engine import SessionLocal
from database.models import WatchedCoin
from database.crud import log_event
from monitor.lp_detector import calculate_score

logger = logging.getLogger(__name__)

DEGRADE_REMOVE_SECONDS = DEGRADE_REMOVE_H * 3600


async def _fetch_dex_data(mint: str) -> dict | None:
    """Quick DexScreener lookup."""
    async with httpx.AsyncClient(timeout=8) as client:
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


async def check_one_coin(coin: WatchedCoin) -> dict | None:
    """
    Check a single coin. Returns dict of updated fields, or None if no change.
    """
    now = int(time.time())
    changes = {}
    events = []

    # Fetch fresh DexScreener data (FREE — no Birdeye calls in routine health checks)
    # Birdeye is only used during deep scan or user-click to save API budget
    dex = await _fetch_dex_data(coin.mint)
    new_mcap = coin.mcap or 0
    new_liq = coin.liq or 0
    new_holders = coin.holders or 0

    if dex:
        new_mcap = dex.get("fdv", 0) or dex.get("marketCap", 0) or 0
        liq_data = dex.get("liquidity", {})
        new_liq = liq_data.get("usd", 0) if isinstance(liq_data, dict) else 0

    # Update metrics
    changes["mcap"] = new_mcap
    changes["liq"] = new_liq
    changes["holders"] = new_holders
    changes["last_checked"] = now

    # Recalculate T2 (same thresholds as lp_detector)
    t2_pass = new_mcap >= MIN_MCAP_T2 and new_liq >= MIN_LIQ_T2 and new_holders >= MIN_HOLDERS_T2
    if t2_pass != coin.filter_t2:
        changes["filter_t2"] = t2_pass

    # Recalculate score (shared formula)
    pass_count = sum([coin.filter_t0, coin.filter_t1, t2_pass, coin.filter_t3, coin.filter_t4])
    new_score = calculate_score(pass_count, new_holders, new_mcap)
    changes["score"] = new_score

    # Status transitions
    old_status = coin.status

    if old_status == "active":
        if not t2_pass or new_score < SCORE_ACTIVE_MIN:
            # Degrade
            changes["status"] = "degraded"
            changes["degraded_at"] = now
            reasons = []
            if new_mcap < MIN_MCAP_T2:
                reasons.append(f"mcap=${new_mcap:,.0f}")
            if new_liq < MIN_LIQ_T2:
                reasons.append(f"liq=${new_liq:,.0f}")
            if new_holders < MIN_HOLDERS_T2:
                reasons.append(f"holders={new_holders}")
            events.append(("warn", f"Degraded — {', '.join(reasons) or f'score < {SCORE_ACTIVE_MIN}'}"))
        elif pass_count >= 4 and new_score >= 70:
            # Still active, log ok
            events.append(("ok", f"Check OK. Score: {new_score}"))

    elif old_status == "degraded":
        # Check if recovered
        if t2_pass and new_score >= SCORE_RECOVER:
            changes["status"] = "active"
            changes["degraded_at"] = None
            events.append(("ok", f"Recovered! Score: {new_score}"))
        else:
            # Check if should remove (degraded > 24h)
            degraded_at = coin.degraded_at or now
            if (now - degraded_at) > DEGRADE_REMOVE_SECONDS:
                changes["status"] = "removed"
                events.append(("dead", f"Removed — degraded for >24h"))

    return {"changes": changes, "events": events, "score": new_score}


async def run_health_check():
    """One health check cycle across all active + degraded coins."""
    db = SessionLocal()
    try:
        coins = db.query(WatchedCoin).filter(
            WatchedCoin.status.in_(["active", "degraded", "pending"])
        ).all()

        if not coins:
            logger.info("  No coins to check")
            return

        logger.info(f"🏥 Health check: {len(coins)} coins")
        checked = 0

        # Separate by status for different processing
        pending_coins = [c for c in coins if c.status == "pending"]
        active_coins = [c for c in coins if c.status in ("active", "degraded")]

        # Process pending coins: refresh DexScreener, auto-promote or auto-remove
        for coin in pending_coins:
            try:
                now = int(time.time())
                age_h = (now - (coin.lp_added_at or coin.last_checked or now)) / 3600

                # Auto-remove stale pending coins
                if age_h > PENDING_MAX_AGE_H:
                    coin.status = "removed"
                    db.commit()
                    log_event(db, coin.mint, coin.symbol, "dead",
                              f"Pending expired ({age_h:.1f}h > {PENDING_MAX_AGE_H}h)", score=coin.score)
                    checked += 1
                    continue

                # Refresh DexScreener only (free)
                dex = await _fetch_dex_data(coin.mint)
                if dex:
                    new_mcap = dex.get("fdv", 0) or dex.get("marketCap", 0) or 0
                    liq_data = dex.get("liquidity", {})
                    new_liq = liq_data.get("usd", 0) if isinstance(liq_data, dict) else 0
                    coin.mcap = new_mcap
                    coin.liq = new_liq
                    coin.last_checked = now
                    coin.score = calculate_score(1, 0, new_mcap)
                    db.commit()

                    # Auto-promote if liquidity meets threshold
                    if new_liq >= PENDING_AUTO_PROMOTE_LIQ:
                        from monitor.lp_detector import run_deep_scan
                        logger.info(f"  ⬆️ Auto-promote {coin.symbol} — liq=${new_liq:,.0f}")
                        await run_deep_scan(coin.mint)

                checked += 1
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error checking pending {coin.symbol}: {e}")
                db.rollback()

        # Process active/degraded coins (existing logic)
        coins = active_coins
        for coin in coins:
            try:
                result = await check_one_coin(coin)
                if not result:
                    continue

                # Capture old status BEFORE applying changes
                old_status = coin.status

                # Apply changes
                for key, val in result["changes"].items():
                    setattr(coin, key, val)
                db.commit()

                # Log events + Telegram alerts
                new_status = result["changes"].get("status", old_status)
                for etype, msg in result["events"]:
                    log_event(db, coin.mint, coin.symbol, etype, msg, score=result["score"])

                # Telegram alerts for important events
                from monitor.telegram_alert import alert_status_change, alert_signal
                if new_status != old_status:
                    await alert_status_change(
                        coin.symbol, coin.mint, old_status, new_status,
                        result["events"][-1][1] if result["events"] else "",
                        result["score"],
                    )
                for etype, msg in result["events"]:
                    if etype == "sm":
                        await alert_signal(coin.symbol, coin.mint, "smart_money_in", msg, result["score"])
                    elif etype == "out":
                        await alert_signal(coin.symbol, coin.mint, "smart_money_out", msg, result["score"])

                checked += 1
                # Rate limit between coins
                await asyncio.sleep(1.5)

            except Exception as e:
                logger.error(f"Error checking {coin.symbol}: {e}")
                db.rollback()

        logger.info(f"  ✅ Checked {checked}/{len(coins)} coins")

    except Exception as e:
        logger.error(f"Health check error: {e}")
    finally:
        db.close()


async def health_checker_loop(interval_seconds: int = 120):
    """Background loop for periodic health checks."""
    logger.info(f"🏥 Health Checker started (interval={interval_seconds}s)")
    # Wait a bit before first run to let LP detector populate some coins
    await asyncio.sleep(10)
    while True:
        try:
            await run_health_check()
        except Exception as e:
            logger.error(f"Health checker loop error: {e}")
        await asyncio.sleep(interval_seconds)
