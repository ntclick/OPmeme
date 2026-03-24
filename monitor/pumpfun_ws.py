# monitor/pumpfun_ws.py — Real-time pump.fun migration detection via WebSocket
#
# pumpportal.fun cung cấp WS API public (không cần key):
#   - subscribeNewToken: coin mới tạo trên pump.fun
#   - subscribeMigration: coin graduate (bonding curve complete → add LP on Raydium)
#
# Flow:
#   WS event "migrate" → process_candidate() → T0→T1→T2→T3→T4 → DB + Telegram
#
# Latency: <1 giây sau khi migrate (vs 30s poll Helius)

import asyncio
import json
import logging
import time

import websockets

logger = logging.getLogger(__name__)

WS_URL = "wss://pumpportal.fun/api/data"
RECONNECT_DELAY = 5  # seconds


async def subscribe_migrations(callback):
    """
    Subscribe to pump.fun migration events via WebSocket.

    callback: async function(mint, dex_name, helius_ts)
    """
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                # Subscribe to migration events (coin graduate → DEX)
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                logger.info("🔌 pump.fun WebSocket connected — listening for migrations")

                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    tx_type = data.get("txType", "")
                    if tx_type != "migrate":
                        continue

                    mint = data.get("mint")
                    if not mint:
                        continue

                    ts = data.get("timestamp") or int(time.time())
                    pool = data.get("pool") or "Unknown"

                    logger.info(f"🚀 WS migration: {mint[:8]}... → {pool}")

                    try:
                        await callback(
                            mint=mint,
                            dex_name="Raydium",  # pump.fun graduates go to Raydium
                            helius_ts=ts,
                        )
                    except Exception as e:
                        logger.error(f"Callback error for {mint[:8]}...: {e}")

        except websockets.ConnectionClosed as e:
            logger.warning(f"pump.fun WS closed: {e}, reconnecting in {RECONNECT_DELAY}s")
        except Exception as e:
            logger.warning(f"pump.fun WS error: {e}, reconnecting in {RECONNECT_DELAY}s")

        await asyncio.sleep(RECONNECT_DELAY)


async def pumpfun_ws_loop():
    """Start pump.fun WebSocket listener — auto-reconnect."""
    from monitor.lp_detector import process_candidate
    logger.info("🔌 Starting pump.fun WebSocket listener")
    await subscribe_migrations(process_candidate)
