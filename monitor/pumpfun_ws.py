# monitor/pumpfun_ws.py — Real-time pump.fun migration detection via WebSocket
#
# pumpportal.fun WS API (public, no auth):
#   - subscribeMigration: coin graduate → add LP on DEX
#
# Flow:
#   "migrate" → process_candidate_light() → status="pending" (free APIs only)
#
# NOTE: Không subscribe "create" vì pump.fun tạo ~1000 coin/phút → tràn DB.
#       Chỉ bắt migrate events = coin đã graduate, có LP trên DEX.

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
