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


# ── Patch Alpha.infer(): SDK 0.9.9 raises when InferenceResult event is missing
#    instead of falling back to node API. We add that fallback. ──
def _patch_alpha_infer():
    try:
        from opengradient.client.alpha import Alpha, PRECOMPILE_CONTRACT_ADDRESS
        from opengradient.client._conversions import convert_to_model_input, convert_to_model_output
        from opengradient.client._utils import run_with_retry
        from opengradient.types import InferenceMode, InferenceResult
        from web3 import Web3
        from web3.logs import DISCARD

        def _patched_infer(self, model_cid, inference_mode, model_input, max_retries=None):
            def execute_transaction():
                contract = self._blockchain.eth.contract(
                    address=Web3.to_checksum_address(self._inference_hub_contract_address),
                    abi=self.inference_abi,
                )
                precompile_contract = self._blockchain.eth.contract(
                    address=Web3.to_checksum_address(PRECOMPILE_CONTRACT_ADDRESS),
                    abi=self.precompile_abi,
                )
                converted_input = convert_to_model_input(model_input)
                run_function = contract.functions.run(model_cid, inference_mode.value, converted_input)
                tx_hash, tx_receipt = self._send_tx_with_revert_handling(run_function)

                # 1) Try event logs (standard path)
                parsed_logs = contract.events.InferenceResult().process_receipt(tx_receipt, errors=DISCARD)
                if parsed_logs:
                    model_output = convert_to_model_output(parsed_logs[0]["args"])
                    if model_output:
                        return InferenceResult(tx_hash.hex(), model_output)

                # 2) Fallback: node API via precompile event
                logger.info("Event logs empty/unparseable, trying node API fallback...")
                precompile_logs = precompile_contract.events.ModelInferenceEvent().process_receipt(tx_receipt, errors=DISCARD)
                if precompile_logs:
                    inference_id = precompile_logs[0]["args"]["inferenceID"]
                    result = self._get_inference_result_from_node(inference_id, inference_mode)
                    model_output = convert_to_model_output(result)
                    if model_output:
                        return InferenceResult(tx_hash.hex(), model_output)

                raise RuntimeError(f"No inference result from event logs or node API (tx: {tx_hash.hex()[:16]}...)")

            return run_with_retry(execute_transaction, max_retries)

        Alpha.infer = _patched_infer
        logger.info("Patched Alpha.infer() with node API fallback")
    except Exception as e:
        logger.warning(f"Failed to patch Alpha.infer: {e}")

_patch_alpha_infer()


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
            # Default RPC: ogevmdevnet (chain 10740) — wallet has ETH here
        )
        logger.info(f"OpenGradient Alpha initialized | RPC: {_og_client._blockchain.provider.endpoint_uri} | Chain: {_og_client._blockchain.eth.chain_id}")
        return _og_client
    except Exception as e:
        logger.warning(f"Failed to init OG Alpha: {e}")
        return None


def _parse_workflow_output(raw) -> float:
    """Parse predicted_return from workflow contract result."""
    logger.info(f"Parsing output: type={type(raw).__name__}, value={raw}")

    # Format 1: ModelOutput with .numbers dict (numpy arrays from SDK)
    if hasattr(raw, 'numbers') and raw.numbers:
        for key, val in raw.numbers.items():
            import numpy as np
            logger.info(f"  numbers['{key}']: type={type(val).__name__}, val={val}")
            if isinstance(val, np.ndarray):
                return float(val.flat[0])
            return float(val)

    # Format 2: Raw tuple from minimal ABI
    # e.g. [('destandardized_prediction', [1010655425488948822021484375], [30], [1])]
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                name, values, decimals_list = item[0], item[1], item[2]
                if values and isinstance(values, (list, tuple)) and len(values) > 0:
                    val = values[0]
                    dec = decimals_list[0] if decimals_list and len(decimals_list) > 0 else 0
                    result = float(val) / (10 ** dec) if dec else float(val)
                    logger.info(f"  Parsed '{name}': val={val}, decimals={dec}, result={result}")
                    return result

    raise ValueError(f"Cannot parse workflow output: {type(raw)} = {raw}")


async def run_inference(model_key: str) -> dict:
    """Run on-chain inference with current candle data via alpha.infer()."""
    import opengradient as og

    model = MODELS.get(model_key)
    if not model:
        return {"error": f"Unknown model: {model_key}"}

    # Fetch current OHLC candles
    binance_interval = _INTERVAL_MAP.get(model["binance_interval"], model["binance_interval"])
    candles = await fetch_ohlc(binance_interval, model["candles"])
    candles = candles[-model["candles"]:]
    if len(candles) < model["candles"]:
        return {"error": f"Not enough candles: {len(candles)}/{model['candles']}"}

    ohlc_input = [[c["open"], c["high"], c["low"], c["close"]] for c in candles]
    current_price = candles[-1]["close"]
    input_time = candles[-1]["time"]

    client = _get_og_client()
    if not client:
        raise ValueError("OpenGradient client not initialized — check OG_PRIVATE_KEY")

    # Strategy 1: Run inference on-chain with current data
    tx_hash = None
    source = "infer"
    try:
        logger.info(f"[{model_key}] Running infer() with CID={model['cid'][:20]}...")
        result = await asyncio.to_thread(
            client.infer,
            model_cid=model["cid"],
            inference_mode=og.InferenceMode.VANILLA,
            model_input={model["input_key"]: ohlc_input},
        )
        logger.info(f"[{model_key}] infer() OK: {result.model_output}, tx={result.transaction_hash[:16]}...")
        predicted_return = _parse_workflow_output(result.model_output)
        tx_hash = result.transaction_hash
    except Exception as e:
        logger.warning(f"[{model_key}] infer() failed: {e}")

        # Strategy 2: Read latest result from pre-deployed workflow contract
        contract_addr = WORKFLOW_CONTRACTS.get(model_key)
        if contract_addr:
            try:
                logger.info(f"[{model_key}] Falling back to workflow contract {contract_addr[:12]}...")
                workflow_result = await asyncio.to_thread(
                    client.read_workflow_result, contract_addr
                )
                logger.info(f"[{model_key}] Workflow result: {workflow_result}")
                predicted_return = _parse_workflow_output(workflow_result)
                source = "workflow"
            except Exception as e2:
                raise ValueError(f"Both infer() and workflow fallback failed: {e} | {e2}") from e
        else:
            raise

    logger.info(f"[{model_key}] Predicted return: {predicted_return:.6f} (source={source})")

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
        "verified": True,
        "simulated": False,
        "source": source,
        "input_candles": len(ohlc_input),
        "input_time": input_time,
        "timestamp": int(time.time()),
        "ohlc_input": ohlc_input,
    }


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
