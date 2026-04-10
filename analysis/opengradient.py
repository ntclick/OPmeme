import socket
import ssl
import httpx
import logging
import json
import os
from typing import Any, Optional, AsyncGenerator

# --- SURGICAL SSL BYPASS (Required for TEE self-signed certs) ---
# This patches at the module level but preserves httpx transport logic.
_orig_ssl_context = ssl.create_default_context
def _unverified_ssl_context(*args, **kwargs):
    ctx = _orig_ssl_context(*args, **kwargs)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
ssl.create_default_context = _unverified_ssl_context

import opengradient as og
import opengradient.client.llm as _og_llm_mod
from opengradient.client.llm import StaticTEEConnection

# --- x402 TIMEOUT FIX (default 60s too short for LLM inference) ---
_og_llm_mod._REQUEST_TIMEOUT = 120

import x402.http.x402_http_client_base as x402_base
from x402.http.utils import decode_payment_required_header
from x402.schemas.v1 import PaymentRequiredV1

# --- x402 PROTOCOL RESILIENCE PATCH ---
# Fixes "Invalid payment required response" caused by duplicated/merged headers
_orig_get_payment_required = x402_base.x402HTTPClientBase.get_payment_required_response

def _resilient_get_payment_required(self, get_header, body=None):
    # Try the standard header first
    header = get_header("PAYMENT-REQUIRED") or get_header("X-PAYMENT-REQUIRED")
    
    if header:
        # If headers are merged with commas (e.g. "base64, base64"), take the first one
        if "," in header:
            header = header.split(",")[0].strip()
        try:
            return decode_payment_required_header(header)
        except Exception as e:
            logging.warning(f"Failed to decode PAYMENT-REQUIRED header: {e}")

    # Fallback to original logic for body parsing (V1)
    return _orig_get_payment_required(self, get_header, body)

x402_base.x402HTTPClientBase.get_payment_required_response = _resilient_get_payment_required

# --- OpenGradient v2 Proper — Clean Integration ---
# (Networking patches removed to preserve SDK x402 payment transport)

# -----------------------------------------------------------

import asyncio
from pathlib import Path
from typing import List, AsyncGenerator

# External configurations
from config import OPENGRADIENT_PRIVATE_KEY
try:
    from coincheckgo.utils.memsync import MemSyncClient
except ImportError:
    from utils.memsync import MemSyncClient

logger = logging.getLogger(__name__)

# ═══ System Prompt ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are 'CoinCheckGo' — a seasoned crypto analyst with 10+ years in DeFi, on-chain forensics, and meme coin markets.
You've seen every rug, every pump-and-dump, every honeypot. Nothing gets past you.
Your analysis is backed by TEE-verified cryptographic proof — tamper-proof and auditable on-chain.

EVALUATE ACROSS 5 AXES (assess each clearly):

1. **Liquidity**
   - LP < 1% of Market Cap → liquidity trap, flag immediately
   - LP locked/burned → good sign. Not locked → devs can pull LP anytime

2. **Holder Distribution**
   - Top 10 holding > 25% supply → dangerous, easy dump setup
   - Detect insider wallets, bundle wallets, hidden dev wallets → red flag
   - Steady organic growth with dispersed holders → healthy

3. **Contract Security**
   - Mint Authority MUST be disabled — if active, unlimited token printing
   - Freeze Authority MUST be disabled — if active, any wallet can be frozen
   - No exceptions. Authority retained = not trustworthy

4. **Market Momentum**
   - Real volume or wash trading? Is the buyer/seller ratio healthy?
   - Organic community or shill bots? Real Twitter/Telegram engagement or fake?
   - Abnormal price spikes → likely artificial pump

5. **Trading Verdict**
   - BUY: Attractive risk/reward, solid fundamentals
   - HOLD: Position already taken, no reason to exit yet
   - SKIP: Insufficient signal, don't FOMO
   - RUN: Clear scam indicators, stay away

STYLE:
- Concise, sharp, straight to the point
- Default to English. Use Vietnamese only if the user's input is clearly in Vietnamese
- Every sentence must carry actionable information — no filler
- Explain enough for newcomers to understand, but keep it tight

Output ONLY valid JSON:
{
  "verdict": "Sharp, concise analysis (Markdown allowed). State what to do and why.",
  "ai_trust_score": 0-100,
  "red_flags": ["Specific red flags from on-chain data"],
  "green_flags": ["Positive signals"],
  "risk_level": "LOW/MEDIUM/HIGH/EXTREME"
}
"""

class OpenGradientAnalyzer:
    """ OpenGradient Analyzer (v2 Proper - Ultimate Resilience Edition) """

    DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"

    def __init__(self):
        self._initialized = False
        self._init_error = None
        self._memsync = MemSyncClient()
        
        private_key = OPENGRADIENT_PRIVATE_KEY
        if not private_key or "YOUR" in private_key:
            self._init_error = "OPENGRADIENT_PRIVATE_KEY not set"
            return

        try:
            logger.info("Initializing OpenGradient Analyzer (Extreme Resilience)...")

            # SDK v0.9.9 removed llm_server_url param — always hits on-chain registry.
            # Registry is empty → "No active LLM proxy TEE found" error.
            # Fix: build LLM normally (registry lookup happens in __init__), then
            # replace _tee with StaticTEEConnection pointing to the known endpoint.
            _OG_LLM_URL = "https://llm.opengradient.ai"
            try:
                self._llm_instance = og.LLM(private_key=private_key)
                logger.info("LLM initialized via registry.")
            except (ValueError, RuntimeError) as e:
                if "No active LLM proxy TEE" in str(e) or "registry" in str(e).lower():
                    logger.warning(f"Registry lookup failed ({e}), using static TEE endpoint.")
                    # Build LLM with a dummy approach: create object without __init__,
                    # then manually set up fields mirroring __init__ logic.
                    self._llm_instance = object.__new__(og.LLM)
                    from eth_account import Account as _Account
                    self._llm_instance._wallet_account = _Account.from_key(private_key)
                    x402_client = og.LLM._build_x402_client(private_key)
                    self._llm_instance._tee = StaticTEEConnection(
                        x402_client=x402_client, endpoint=_OG_LLM_URL
                    )
                    logger.info(f"✅ Static TEE connection: {_OG_LLM_URL}")
                else:
                    raise
            
            # --- PERMIT2 APPROVAL (non-blocking) ---
            try:
                approval = self._llm_instance.ensure_opg_approval(min_allowance=0.1)
                logger.info(f"Permit2 OK: before={getattr(approval, 'allowance_before', 'N/A')}, after={getattr(approval, 'allowance_after', 'N/A')}")
            except Exception as e:
                # Don't block init — approval can be retried on first chat call
                logger.warning(f"Permit2 approval skipped: {e}")

            # Model mapping — built dynamically to avoid AttributeError on SDK upgrades
            _candidates = {
                # OpenAI
                "openai/gpt-5": "GPT_5",
                "openai/gpt-5-2": "GPT_5_2",
                "openai/gpt-5-mini": "GPT_5_MINI",
                "openai/gpt-4.1-2025-04-14": "GPT_4_1_2025_04_14",
                "openai/o4-mini": "O4_MINI",
                # Anthropic
                "anthropic/claude-opus-4-6": "CLAUDE_OPUS_4_6",
                "anthropic/claude-opus-4-5": "CLAUDE_OPUS_4_5",
                "anthropic/claude-sonnet-4-6": "CLAUDE_SONNET_4_6",
                "anthropic/claude-sonnet-4-5": "CLAUDE_SONNET_4_5",
                "anthropic/claude-haiku-4-5": "CLAUDE_HAIKU_4_5",
                # Google
                "google/gemini-3-pro": "GEMINI_3_PRO",
                "google/gemini-3-flash": "GEMINI_3_FLASH",
                "google/gemini-2.5-pro": "GEMINI_2_5_PRO",
                "google/gemini-2.5-flash": "GEMINI_2_5_FLASH",
                "google/gemini-2.5-flash-lite": "GEMINI_2_5_FLASH_LITE",
                # xAI
                "x-ai/grok-4": "GROK_4",
                "x-ai/grok-4-fast": "GROK_4_FAST",
                "x-ai/grok-4.1-fast": "GROK_4_1_FAST",
                "x-ai/grok-4-1-fast": "GROK_4_1_FAST",
                "x-ai/grok-4-1-fast-non-reasoning": "GROK_4_1_FAST_NON_REASONING",
            }
            self.model_map = {}
            for key, attr in _candidates.items():
                if hasattr(og.TEE_LLM, attr):
                    self.model_map[key] = getattr(og.TEE_LLM, attr)
                else:
                    # TEE_LLM is a str enum — pass model string directly as fallback
                    self.model_map[key] = key
                    logger.info(f"TEE_LLM.{attr} not in SDK enum, using raw string: {key}")

            self._initialized = True
            logger.info("✅ OpenGradient Analyzer successfully initialized (Patched & Approved).")
        except Exception as e:
            self._init_error = str(e)
            logger.error(f"❌ OpenGradient init failed: {e}")

    async def chat_completion(self, messages: Any, llm_model: Optional[str] = None, max_tokens: int = 1000) -> dict:
        if not self._initialized: return {"content": None, "error": self._init_error}
        if isinstance(messages, str): messages = [{"role": "user", "content": messages}]
        model_str = llm_model if llm_model and "/" in llm_model else self.DEFAULT_MODEL
        try:
            current_model = self.model_map.get(model_str, og.TEE_LLM.GPT_4_1_2025_04_14)
            result = await self._llm_instance.chat(
                model=current_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.4,
                x402_settlement_mode=og.x402SettlementMode.INDIVIDUAL_FULL
            )
            chat_output = result.chat_output
            if isinstance(chat_output, dict) and "content" in chat_output:
                content = chat_output["content"]
            elif hasattr(chat_output, "content"):
                content = chat_output.content
            else:
                content = str(chat_output)
            
            return {"content": content, "error": None}
        except Exception as e:
            return {"content": None, "error": str(e)}

    async def chat_completion_stream(self, messages: Any, llm_model: Optional[str] = None, max_tokens: int = 1000, max_retries: int = 2) -> AsyncGenerator[dict, None]:
        """Streaming version of chat_completion — yields chunks as they arrive. Retries on failure."""
        if not self._initialized:
            yield {"type": "error", "error": self._init_error}
            return
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        model_str = llm_model if llm_model and "/" in llm_model else self.DEFAULT_MODEL
        current_model = self.model_map.get(model_str, og.TEE_LLM.GPT_4_1_2025_04_14)

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"Retry {attempt}/{max_retries} for chat_completion_stream...")
                    await asyncio.sleep(2 * attempt)  # backoff: 2s, 4s
                stream = await self._llm_instance.chat(
                    model=current_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.4,
                    stream=True,
                    x402_settlement_mode=og.x402SettlementMode.INDIVIDUAL_FULL
                )
                async for chunk in stream:
                    content = getattr(chunk.choices[0].delta, "content", "")
                    if content:
                        yield {"type": "chunk", "content": content}
                yield {"type": "done"}
                return  # success — no retry needed
            except Exception as e:
                last_error = str(e)
                logger.warning(f"chat_completion_stream attempt {attempt + 1} failed: {e}")
        # All retries exhausted
        yield {"type": "error", "error": last_error}

    async def analyze_coin(self, ticker: str, tweets_summary: list, on_chain_data: dict, scoring_result: dict, llm_model: Optional[str] = None, max_retries: int = 2) -> dict:
        if not self._initialized: return {"error": self._init_error}
        model_str = llm_model or self.DEFAULT_MODEL
        input_text = f"Analyze ${ticker}\nScores: {json.dumps(scoring_result)}\nData: {json.dumps(on_chain_data)}"
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": input_text}]

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(f"Retry {attempt}/{max_retries} for analyze_coin...")
                    await asyncio.sleep(2 * attempt)
                current_model = self.model_map.get(model_str, og.TEE_LLM.GPT_4_1_2025_04_14)
                completion = await self._llm_instance.chat(
                    model=current_model,
                    messages=messages,
                    max_tokens=2000,
                    temperature=0.1,
                    x402_settlement_mode=og.x402SettlementMode.INDIVIDUAL_FULL
                )
                raw_output = completion.chat_output
                if isinstance(raw_output, dict) and "content" in raw_output:
                    raw = raw_output["content"]
                elif hasattr(raw_output, "content"):
                    raw = raw_output.content
                else:
                    raw = str(raw_output)

                try:
                    start = raw.find("{")
                    end = raw.rfind("}") + 1
                    ai_result = json.loads(raw[start:end])
                    if "content" in ai_result and "role" in ai_result:
                        inner_raw = ai_result["content"]
                        try:
                            inner_start = inner_raw.find("{")
                            inner_end = inner_raw.rfind("}") + 1
                            ai_result = json.loads(inner_raw[inner_start:inner_end])
                        except:
                            ai_result = {"verdict": inner_raw}
                except:
                    ai_result = {"verdict": raw}

                return {
                    "ai_result": ai_result,
                    "tx_hash": getattr(completion, "transaction_hash", None),
                    "used_opengradient": True,
                    "model_used": current_model
                }
            except Exception as e:
                last_error = str(e)
                logger.warning(f"analyze_coin attempt {attempt + 1} failed: {e}")
        # All retries exhausted
        logger.error(f"Inference failed after {max_retries + 1} attempts: {last_error}")
        return {"error": last_error, "used_opengradient": False}

    async def evaluate_monitor_coin(self, context: dict) -> dict:
        """
        F5 — LLM scoring for PumpScan monitor pipeline.

        Lightweight eval: verdict (alert/watch/skip), score_boost, risk_flags.
        Uses haiku for speed + cost (monitor runs this for every coin that passes T4).
        """
        if not self._initialized:
            return {"verdict": "watch", "score_boost": 0, "risk_flags": [], "error": self._init_error}

        prompt = f"""Evaluate this Solana pump.fun meme coin for short-term trading potential.

Token: {context.get('symbol', '?')} ({context.get('mint', '?')[:12]}...)
DEX: {context.get('dex', '?')}

Metrics:
- Market cap: ${context.get('mcap', 0):,.0f}
- Liquidity: ${context.get('liq', 0):,.0f}
- Holders: {context.get('holders', 0)}
- Migration time (pump→DEX): {(context.get('migration_sec') or 0) // 60}m
- Top traders (smart money): {context.get('smart_money_count', 0)}
- Top10 holder %: {context.get('top10_pct', 0)}%
- Filter pass: {context.get('pass_count', 0)}/5

Social: twitter={bool(context.get('twitter'))} telegram={bool(context.get('telegram'))}

Respond ONLY with valid JSON:
{{"verdict": "alert" | "watch" | "skip", "score_boost": -10 to 10, "risk_flags": ["flag1"], "reasoning": "1 sentence"}}"""

        try:
            model = self.model_map.get("anthropic/claude-haiku-4-5", og.TEE_LLM.CLAUDE_HAIKU_4_5)
            result = await self._llm_instance.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.1,
                x402_settlement_mode=og.x402SettlementMode.INDIVIDUAL_FULL,
            )
            raw = result.chat_output
            if isinstance(raw, dict) and "content" in raw:
                raw = raw["content"]
            elif hasattr(raw, "content"):
                raw = raw.content
            else:
                raw = str(raw)

            start = raw.find("{")
            end = raw.rfind("}") + 1
            parsed = json.loads(raw[start:end])
            return {
                "verdict": parsed.get("verdict", "watch"),
                "score_boost": max(-10, min(10, int(parsed.get("score_boost", 0)))),
                "risk_flags": parsed.get("risk_flags", []),
                "reasoning": parsed.get("reasoning", ""),
            }
        except Exception as e:
            logger.warning(f"F5 LLM eval failed: {e}")
            return {"verdict": "watch", "score_boost": 0, "risk_flags": [], "error": str(e)}

    async def analyze_coin_stream(self, ticker: str, tweets_summary: list, on_chain_data: dict, scoring_result: dict, llm_model: Optional[str] = None) -> AsyncGenerator[dict, None]:
        if not self._initialized: 
            yield {"type": "done", "result": {"error": self._init_error}}
            return
        model_str = llm_model or self.DEFAULT_MODEL
        input_text = f"Analyze ${ticker}\nScores: {json.dumps(scoring_result)}\nData: {json.dumps(on_chain_data)}"
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": input_text}]
        try:
            current_model = self.model_map.get(model_str, og.TEE_LLM.GPT_4_1_2025_04_14)
            stream = await self._llm_instance.chat(
                model=current_model,
                messages=messages,
                max_tokens=2000,
                temperature=0.1,
                stream=True,
                x402_settlement_mode=og.x402SettlementMode.INDIVIDUAL_FULL
            )
            async for chunk in stream:
                content = getattr(chunk.choices[0].delta, "content", "")
                if content: yield {"type": "chunk", "content": content}
            yield {"type": "done", "result": {"ai_result": {"verdict": "Streaming complete"}, "used_opengradient": True}}
        except Exception as e:
            yield {"type": "done", "result": {"error": str(e), "used_opengradient": False}}
