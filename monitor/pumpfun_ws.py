# monitor/pumpfun_ws.py — Real-time pump.fun migration detection via WebSocket
#
# pumpportal.fun WS API (public, no auth):
#   - subscribeMigration: coin graduate → add LP on DEX
#
# Flow:
#   "migrate" → process_candidate_light() → status="pending" (free APIs only)
#
# NOTE: Do NOT subscribe "create" — pump.fun creates ~1000 coins/min, would flood DB.
#       Only catch migrate events = coins that graduated and have LP on DEX.

import asyncio
import json
import logging
import time

import websockets

from database.engine import SessionLocal
from database.models import WatchedCoin

logger = logging.getLogger(__name__)

WS_URL = "wss://pumpportal.fun/api/data"
RECONNECT_DELAY = 5  # seconds

# Stats
_ws_stats = {"connected_at": 0, "migrations": 0, "errors": 0}


def get_ws_stats() -> dict:
    """Return WebSocket connection stats."""
    return {**_ws_stats, "uptime_min": (int(time.time()) - _ws_stats["connected_at"]) // 60 if _ws_stats["connected_at"] else 0}


async def pumpfun_ws_loop():
    """Listen to pump.fun WebSocket for migration events only."""
    from monitor.lp_detector import process_candidate_light

    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                # Only subscribe to migrations — NOT newToken (too noisy)
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                _ws_stats["connected_at"] = int(time.time())
                logger.info("🔌 pump.fun WS connected — subscribed to migration events")

                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    tx_type = data.get("txType", "")
                    mint = data.get("mint")
                    if not mint:
                        continue

                    if tx_type == "migrate":
                        ts = data.get("timestamp") or int(time.time())
                        dex = data.get("dex") or "Raydium"

                        # Skip if already in DB
                        db = SessionLocal()
                        try:
                            existing = db.query(WatchedCoin).filter(WatchedCoin.mint == mint).first()
                            if existing:
                                continue
                        finally:
                            db.close()

                        _ws_stats["migrations"] += 1
                        logger.info(f"🚀 WS migrate: {mint[:8]}... → {dex}")
                        try:
                            await process_candidate_light(mint=mint, dex_name=dex, helius_ts=ts)
                        except Exception as e:
                            _ws_stats["errors"] += 1
                            logger.error(f"Migration callback error {mint[:8]}...: {e}")

        except websockets.ConnectionClosed as e:
            logger.warning(f"pump.fun WS closed: {e}, reconnecting in {RECONNECT_DELAY}s")
        except Exception as e:
            logger.warning(f"pump.fun WS error: {e}, reconnecting in {RECONNECT_DELAY}s")

        await asyncio.sleep(RECONNECT_DELAY)


async def ws_seed_collect(duration_sec: int = 120) -> int:
    """
    One-shot: connect to WS, collect migrations for `duration_sec`, return count.
    Used at startup to seed DB when empty, and for on-demand /api/monitor/seed endpoint.
    """
    from monitor.lp_detector import process_candidate_light

    count = 0
    start = time.time()
    logger.info(f"🌱 WS seed: collecting migrations for {duration_sec}s...")

    try:
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
            await ws.send(json.dumps({"method": "subscribeMigration"}))

            while time.time() - start < duration_sec:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(raw)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

                if data.get("txType") != "migrate":
                    continue

                mint = data.get("mint")
                if not mint:
                    continue

                # Skip if already in DB
                db = SessionLocal()
                try:
                    if db.query(WatchedCoin).filter(WatchedCoin.mint == mint).first():
                        continue
                finally:
                    db.close()

                ts = data.get("timestamp") or int(time.time())
                dex = data.get("dex") or "Raydium"

                try:
                    await process_candidate_light(mint=mint, dex_name=dex, helius_ts=ts)
                    count += 1
                    logger.info(f"  🌱 Seed #{count}: {mint[:8]}... → {dex}")
                except Exception as e:
                    logger.error(f"  Seed error {mint[:8]}...: {e}")

    except Exception as e:
        logger.warning(f"WS seed error: {e}")

    logger.info(f"🌱 WS seed done: {count} coins in {int(time.time() - start)}s")
    return count
