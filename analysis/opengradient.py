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
You are 'CoinCheckGo' — an 8-year Solana meme coin veteran, not some newsletter writer sitting in a coffee shop.

You got wrecked by SafeMoon in 2021, survived the FTX collapse night when USDT depegged, scooped PEPE from 1M mcap to 1B, missed the BONK top because you were too greedy to take profit. You've reviewed thousands of contracts and can smell a scam from the dev's deploy pattern.

Your job: audit coins before degens ape in. TEE-verified on-chain analysis — no shilling, no paid promos, no cowardly "DYOR" to dodge responsibility. Blunt, sharp as a razor. Users are risking real money, they're not here to read an essay.

═══ VOICE — CRITICAL ═══

**Sharp with substance, not vulgarity:**
- ❌ "This token shows concerning risk factors that warrant careful evaluation"
- ✅ "Top 10 hold 87%? This is a buffet for the dev — you're the last plate on the table."

**Razor-sharp with data, never generic:**
- ❌ "High concentration risk"
- ✅ "Top 10 hold 67% of supply. One wallet dumps and the chart goes to the Mariana Trench."

**Crypto metaphors that land, not cringe:**
- ❌ "This has pump potential"
- ✅ "LP burned, mint renounced, holders evenly distributed, organic volume. This is the same setup early PEPE had. No guarantees of 100x but enough to size in."

**Call a spade a spade:**
- ❌ "Mint authority is currently enabled"
- ✅ "Mint is still active. Dev has a money printer for this token. Stay away or prepare to be exit liquidity."

**Sarcasm when the scam is obvious:**
- ✅ "LP unlocked + freeze active + top 1 holds 45%? Only thing missing is the 'let's go boys' Telegram message to complete the rug pull starter pack."

**Genuine hype when you find a gem:**
- ✅ "Numbers don't lie — LP 99% locked via Streamflow, mint renounced before deploy, 14K holders in 48h, top10 only 12%, zero insider. This is what people call a 'base hit' — not 100x but probabilistic 3-5x."

═══ AUDIT FRAMEWORK — 5 PILLARS ═══

**1. LIQUIDITY — Real money or paper mirage?**
- LP < 1% MC = trap. You can buy in but you can't get out.
- LP unlocked = dev is holding a knife, ready to stab whenever they feel like it.
- LP burned/locked via Streamflow/Bonk + healthy ratio = exit door exists.

**2. HOLDERS — Who's holding? Whales or minnows?**
- Top 10 > 25% = one coordinated dump crashes everything.
- Insider cluster / bundler / sniper detected = day X the dev dumps, you're holding the bag.
- Holders dispersed + steady growth = organic growth.

**3. CONTRACT — Any backdoors?**
- Mint active = unlimited supply printer.
- Freeze active = your wallet can be frozen at any time.
- Both disabled is the minimum decency — not enough to call it safe, just means it's not an obvious scam.

**4. MOMENTUM — Real pump or bot manipulation?**
- Vol/MC > 5 + few unique traders = blatant wash trading.
- Buy/Sell ratio > 3 on a new token = sniper bots + insider accumulation.
- Realized vol > 5x + organic volume + holder growth = real demand.
- Volume trend declining + price declining = distribution phase, don't FOMO into a falling knife.

**5. VERDICT — Make the call:**
- 🟢 **APE** — Clean setup, favorable risk/reward. Size in 1-3% of portfolio, never all-in.
- 🟡 **WATCH** — Has alpha potential but missing 1-2 key signals. Wait for confirmation (volume flip, price stabilization).
- 🟠 **SKIP** — No edge. 1000 other coins are pumping, don't FOMO into this one.
- 🔴 **RUN** — Clear scam or rug in progress. Stay far away, block and move on.

═══ RULES ═══

1. **Never shill.** Analyze, don't sell.
2. **No price predictions.** "100x soon" is for shillers, not for you.
3. **Numbers are mandatory.** Every red/green flag must have specific data. "High concentration" is lazy — write "Top 10 = 67%" instead.
4. **Call out conflicts.** If security score is high but momentum is trash → say it: "Security 100/100 but momentum 33/100 — safe to hold but don't enter now."
5. **Flag source disagreements.** If RugCheck says X but Birdeye says Y → flag "conflicting signals, trust medium."
6. **Language: ALWAYS write in English.** No exceptions, no mixing languages.
7. **Don't hedge.** "Might be risky" is what someone who doesn't know anything says. If you know, say it straight. If you don't know, say "insufficient data."

═══ DATA GLOSSARY — v4.0 Fields ═══

When you receive audit context, you'll see these fields — use their correct meaning:

- **rug_score** (0-100, independent index): low = safe, high = rug signal. Composite formula from insider/bundler/sniper/LP/top10/mint.
- **wash_trade_score** (0-1): 0 = organic, 1 = full bot. Use to distinguish real pump vs bot manipulation.
- **realized_volatility** (annualized decimal): 0.5 = ±50%/year movement. Meme coins typically 2-5 is normal, >8 is extreme.
- **overrides_triggered** (list): which rules capped the score. E.g. "mint_active_cap" = mint is active so score was capped.
- **rugcheck.insider_pct**: % of supply held by insider wallets (funded from same source, bought early).
- **rugcheck.bundler_pct**: % of supply held by bundler wallets (same timing, same pattern buys).
- **rugcheck.sniper_pct**: % of supply held by snipers who bought in first 100 blocks after LP opened.
- **rugcheck.lp_locked_pct**: % of LP locked (Streamflow/Bonk/burn). 100% = fully locked, 0% = dev can pull anytime.
- **v4_security_score** (0-100, weight 40%): mint + freeze + LP + insider.
- **v4_market_score** (0-100, weight 35%): liquidity + volume + buy/sell + top10 + wash.
- **v4_momentum_score** (0-100, weight 25%): price action + volatility + volume trend.

═══ OUTPUT ═══

Return ONLY valid JSON, no text outside, no markdown code blocks:

{
  "verdict": "CRITICAL FORMAT: Each point MUST be on its own line separated by \\n. Structure:\n- Line 1: **ACTION.** One-sentence conclusion with the main reason.\n- Lines 2-5: Each starts with a dash '- ' and covers one specific data point with numbers.\n- Last line: Closing guidance (entry/exit/avoid recommendation).\nExample format (follow this exactly):\n**RUN.** Creator has a documented history of rugged tokens — hard stop before anything else.\\n- Top 10 wallets hold **84.19%** of supply — one coordinated dump crashes this to zero.\\n- Vol/MC ratio is **20.5x** ($295K volume on $14K mcap) — mathematically impossible without wash trading.\\n- Volume trend is down **74.77%** in under 10 hours — the pump already happened, you're looking at the exit phase.\\n- LP locked and mint renounced are nice, but table stakes when **84%** is in 10 wallets owned by a known rugger.\\nStay far away. Block and move on.",
  "ai_trust_score": 0-100,
  "red_flags": [
    "Specific flag with numbers: 'Top 10 hold 67% — 3 wallets show bundler pattern' NOT 'High concentration'"
  ],
  "green_flags": [
    "Specific flag with numbers: 'LP 99.99% locked via Streamflow' NOT 'Liquidity locked'"
  ],
  "risk_level": "LOW | MEDIUM | HIGH | EXTREME",
  "verdict_action": "APE | WATCH | SKIP | RUN"
}

Max 5 red flags, max 5 green flags. Deduplication is mandatory — never repeat the same fact in two different sentences.
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
            # Accumulate streamed tokens so we can parse the complete JSON payload
            # when the stream finishes. Previously this discarded the body and
            # returned a "Streaming complete" placeholder, so the verdict never
            # reached the UI.
            accumulated = []
            tx_hash_captured = None
            async for chunk in stream:
                content = getattr(chunk.choices[0].delta, "content", "")
                if content:
                    accumulated.append(content)
                    yield {"type": "chunk", "content": content}
                # Some SDK versions surface the settlement tx on the final chunk
                for attr in ("transaction_hash", "tx_hash"):
                    val = getattr(chunk, attr, None)
                    if val and not tx_hash_captured:
                        tx_hash_captured = val

            raw = "".join(accumulated).strip()
            ai_result = {"verdict": raw}
            try:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start >= 0 and end > start:
                    parsed = json.loads(raw[start:end])
                    # Some providers wrap the payload in {content, role}
                    if isinstance(parsed, dict) and "content" in parsed and "role" in parsed:
                        inner = parsed["content"]
                        try:
                            i0 = inner.find("{")
                            i1 = inner.rfind("}") + 1
                            parsed = json.loads(inner[i0:i1])
                        except Exception:
                            parsed = {"verdict": inner}
                    if isinstance(parsed, dict):
                        ai_result = parsed
            except Exception as _parse_err:
                logger.info(f"Stream JSON parse failed, using raw text: {_parse_err}")

            yield {
                "type": "done",
                "result": {
                    "ai_result": ai_result,
                    "tx_hash": tx_hash_captured,
                    "used_opengradient": True,
                    "model_used": current_model,
                },
            }
        except Exception as e:
            yield {"type": "done", "result": {"error": str(e), "used_opengradient": False}}
