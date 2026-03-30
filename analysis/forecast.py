# analysis/forecast.py — SUI/USDT Spot Forecasting via OpenGradient ML Models
#
# Models:
#   - og-30min-return-suiusdt: 10 x 30min OHLC candles → predicted 30min return
#   - og-6h-return-suiusdt:    6 x 3h OHLC candles   → predicted 6h return

import ssl
# --- SSL bypass for OpenGradient devnet (self-signed certs) ---
_orig_ssl_context = ssl.create_default_context
def _unverified_ssl_context(*args, **kwargs):
    ctx = _orig_ssl_context(*args, **kwargs)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
ssl.create_default_context = _unverified_ssl_context

import asyncio
import logging
import time
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Model Registry ──────────────────────────────────────────────────────────

MODELS = {
    "30min": {
        "name": "OpenGradient/og-30min-return-suiusdt",
        "cid": os.getenv("OG_MODEL_CID_30MIN", "hJD2Ja3akZFt1A2LT-D_1oxOCz_OtuGYw4V9eE1m39M"),
        "input_key": "open_high_low_close",
        "candles": 10,
        "interval": "30m",
        "binance_interval": "30m",
        "horizon": "30 min",
        "accuracy": 52.7,
        "mse": 0.00007,
        "correlation": 0.057,
    },
    "6h": {
        "name": "OpenGradient/og-6h-return-suiusdt",
        "cid": os.getenv("OG_MODEL_CID_6H", "Uje7jgfWjygXo-TMbw77Jibi_ZVC_eGMODwzMvLXv1c"),
        "input_key": "ohlc_3h_candles",
        "candles": 6,
        "interval": "4h",
        "binance_interval": "4h",
        "horizon": "6 hours",
        "accuracy": 53.0,
        "mse": 0.00081,
        "correlation": 0.12,
    },
}

# ── Binance Public API (no auth needed) ─────────────────────────────────────

BINANCE_BASE = os.getenv("BINANCE_API_BASE", "https://data-api.binance.vision")
BINANCE_KLINES_URL = f"{BINANCE_BASE}/api/v3/klines"
SYMBOL = "SUIUSDT"

# Binance valid intervals — map unsupported ones to nearest valid
_INTERVAL_MAP = {"3h": "4h", "6h": "4h", "12h": "8h"}


async def fetch_ohlc(interval: str, limit: int) -> list[dict]:
    """Fetch OHLC candles from Binance for SUI/USDT."""
    interval = _INTERVAL_MAP.get(interval, interval)
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        resp = await client.get(
            BINANCE_KLINES_URL,
            params={"symbol": SYMBOL, "interval": interval, "limit": limit + 5},
        )
        resp.raise_for_status()
        raw = resp.json()

    candles = []
    for k in raw:
        candles.append({
            "time": int(k[0]) // 1000,  # unix seconds
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    return candles


async def fetch_price_history(interval: str = "15m", limit: int = 200) -> list[dict]:
    """Fetch candle history for chart display."""
    interval = _INTERVAL_MAP.get(interval, interval)
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        resp = await client.get(
            BINANCE_KLINES_URL,
            params={"symbol": SYMBOL, "interval": interval, "limit": limit},
        )
        resp.raise_for_status()
        raw = resp.json()

    return [
        {
            "time": int(k[0]) // 1000,
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]


async def fetch_current_price() -> dict:
    """Fetch current SUI/USDT ticker."""
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        resp = await client.get(
            f"{BINANCE_BASE}/api/v3/ticker/24hr",
            params={"symbol": SYMBOL},
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "price": float(data["lastPrice"]),
        "change_24h": float(data["priceChangePercent"]),
        "high_24h": float(data["highPrice"]),
        "low_24h": float(data["lowPrice"]),
        "volume_24h": float(data["volume"]),
        "quote_volume_24h": float(data["quoteVolume"]),
    }


# ── OpenGradient On-Chain Inference ─────────────────────────────────────────

WORKFLOW_CONTRACTS = {
    "30min": "0xD85BA71f5701dc4C5BDf9780189Db49C6F3708D2",
    "6h":    "0x3C2E4DbD653Bd30F1333d456480c1b7aB122e946",
}

_og_client = None


def _get_og_client():
    """Lazy-init OpenGradient Alpha for on-chain reads."""
    global _og_client
    if _og_client is not None:
        return _og_client

    try:
        import opengradient as og
        from config import OPENGRADIENT_PRIVATE_KEY

        pk = OPENGRADIENT_PRIVATE_KEY
        if not pk or "YOUR" in pk:
            logger.warning("OG_PRIVATE_KEY not set — on-chain inference disabled")
            return None

        # Proxy bypass Cloudflare IP block — only needed locally, not on Railway
        if not os.getenv("RAILWAY_ENVIRONMENT"):
            proxy = "http://uzkijfqe:1h8tfoyq41mt@31.59.20.176:6754/"
            os.environ["HTTPS_PROXY"] = proxy
            os.environ["HTTP_PROXY"]  = proxy
            os.environ["https_proxy"] = proxy
            os.environ["http_proxy"]  = proxy
            logger.info(f"Proxy set: {proxy[:30]}...")
        else:
            logger.info("Railway detected — skipping proxy (DNS fix in entrypoint.sh)")

        os.environ["OG_PRIVATE_KEY"] = pk
        _og_client = og.Alpha(
            private_key=pk,
            rpc_url="https://eth-devnet.opengradient.ai",
        )
        logger.info(f"OpenGradient Alpha initialized | RPC: {_og_client._blockchain.provider.endpoint_uri} | Chain: {_og_client._blockchain.eth.chain_id}")
        return _og_client
    except Exception as e:
        logger.warning(f"Failed to init OG Alpha: {e}")
        return None


def _parse_workflow_output(raw) -> float:
    """Parse predicted_return from workflow contract result.

    raw format: ModelOutput with numbers dict containing numpy arrays,
    e.g. {'Y': array([[-0.00123]])} or similar fixed-point encoding.
    """
    # ModelOutput has .numbers dict
    if hasattr(raw, 'numbers') and raw.numbers:
        for key, val in raw.numbers.items():
            import numpy as np
            if isinstance(val, np.ndarray):
                return float(val.flat[0])
            return float(val)

    # Fallback: try treating raw as tuple (legacy format)
    if isinstance(raw, (list, tuple)) and len(raw) > 0:
        first = raw[0]
        if isinstance(first, (list, tuple)) and len(first) > 0:
            name, values, shape = first[0] if isinstance(first[0], tuple) else (None, first, None)
            if values and isinstance(values, (list, tuple)):
                val, decimals = values[0] if isinstance(values[0], tuple) else (values[0], 0)
                return float(val) / (10 ** decimals) if decimals else float(val)

    raise ValueError(f"Cannot parse workflow output: {type(raw)} = {raw}")


async def run_inference(model_key: str) -> dict:
    """
    Run forecast inference for SUI/USDT.

    Priority: 1) Workflow contract (read state) → 2) infer() → 3) Simulation
    """
    model = MODELS.get(model_key)
    if not model:
        return {"error": f"Unknown model: {model_key}"}

    # Step 1: Fetch candles for metadata + chart
    binance_interval = _INTERVAL_MAP.get(model["binance_interval"], model["binance_interval"])
    try:
        candles = await fetch_ohlc(binance_interval, model["candles"])
        candles = candles[-model["candles"]:]
        if len(candles) < model["candles"]:
            return {"error": f"Not enough candle data: got {len(candles)}, need {model['candles']}"}
    except Exception as e:
        return {"error": f"Failed to fetch OHLC: {e}"}

    ohlc_input = [[c["open"], c["high"], c["low"], c["close"]] for c in candles]
    current_price = candles[-1]["close"]
    input_time = candles[-1]["time"]

    def _build_result(predicted_return, source, tx_hash=None):
        predicted_price = current_price * (1 + predicted_return)
        return {
            "model": model_key,
            "model_name": model["name"],
            "horizon": model["horizon"],
            "current_price": current_price,
            "predicted_return": round(predicted_return * 100, 4),
            "predicted_price": round(predicted_price, 4),
            "direction": "UP" if predicted_return > 0 else "DOWN",
            "confidence": model["accuracy"],
            "tx_hash": tx_hash,
            "verified": source != "simulation",
            "simulated": source == "simulation",
            "source": source,
            "input_candles": len(ohlc_input),
            "input_time": input_time,
            "timestamp": int(time.time()),
            "ohlc_input": ohlc_input,
        }

    client = _get_og_client()

    # Step 2A: Read from workflow contract (no transaction needed)
    contract_addr = WORKFLOW_CONTRACTS.get(model_key)
    if client and contract_addr:
        try:
            logger.info(f"[{model_key}] Reading workflow contract {contract_addr[:12]}...")
            raw = await asyncio.to_thread(client.read_workflow_result, contract_addr)
            predicted_return = _parse_workflow_output(raw)
            logger.info(f"[{model_key}] Workflow result: return={predicted_return:.6f}")
            return _build_result(predicted_return, source="workflow")
        except Exception as e:
            logger.warning(f"[{model_key}] read_workflow_result failed: {e}")

    # Step 2B: Direct infer() (sends transaction — may fail due to TLS)
    if client and model["cid"]:
        try:
            import opengradient as og
            input_key = model.get("input_key", "open_high_low_close")
            logger.info(f"[{model_key}] Running infer() with CID={model['cid'][:20]}...")
            result = await asyncio.to_thread(
                client.infer,
                model_cid=model["cid"],
                inference_mode=og.InferenceMode.VANILLA,
                model_input={input_key: ohlc_input},
            )
            output = result.model_output
            # Parse output — key may be "destandardized_prediction" or similar
            predicted_return = None
            if hasattr(output, 'numbers') and output.numbers:
                for key, val in output.numbers.items():
                    import numpy as np
                    predicted_return = float(val.flat[0]) if hasattr(val, 'flat') else float(val)
                    logger.info(f"[{model_key}] Output key='{key}', value={predicted_return}")
                    break
            elif isinstance(output, dict):
                for key, val in output.items():
                    predicted_return = float(val.flat[0]) if hasattr(val, 'flat') else float(val)
                    logger.info(f"[{model_key}] Output key='{key}', value={predicted_return}")
                    break
            if predicted_return is None:
                raise ValueError(f"Cannot parse model output: {output}")
            logger.info(f"[{model_key}] Inference OK! tx={result.transaction_hash[:16]}...")
            return _build_result(predicted_return, source="infer", tx_hash=result.transaction_hash)
        except Exception as e:
            logger.warning(f"[{model_key}] infer() failed: {e}")

    # Step 3: Simulation fallback
    logger.info(f"[{model_key}] Using simulation mode")
    returns = []
    for i in range(1, len(candles)):
        r = (candles[i]["close"] - candles[i - 1]["close"]) / candles[i - 1]["close"]
        returns.append(r)
    if returns:
        weights = list(range(1, len(returns) + 1))
        predicted_return = sum(r * w for r, w in zip(returns, weights)) / sum(weights) * 0.5
    else:
        predicted_return = 0.0
    return _build_result(predicted_return, source="simulation")


# ── Prediction History (in-memory ring buffer) ─────────────────────────────

_prediction_history: list[dict] = []
MAX_HISTORY = 50


def record_prediction(prediction: dict):
    """Store prediction in ring buffer."""
    _prediction_history.append(prediction)
    if len(_prediction_history) > MAX_HISTORY:
        _prediction_history.pop(0)


def get_prediction_history() -> list[dict]:
    """Return recent predictions, newest first."""
    return list(reversed(_prediction_history))
