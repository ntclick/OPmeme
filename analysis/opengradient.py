# analysis/opengradient.py — OpenGradient SDK wrapper for verifiable AI inference
#
# SDK docs: https://docs.opengradient.ai/developers/sdk/llm.html
# API ref:  https://docs.opengradient.ai/api_reference/python_sdk/
# x402:     https://docs.opengradient.ai/developers/x402/
# Payment:  $OPG tokens on Base Sepolia (chain 84532)
#
# IMPORTANT: The SDK (v0.6.0+) handles x402 payment flow automatically.
# Do NOT monkey-patch x402v2 — it breaks the internal payment client.
# Permit2 approval is needed but use SDK built-in: client.llm.ensure_opg_approval()
# OPG token = 18 decimals → fee per request ≈ 0.000001 OPG (near zero)

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Any, List, AsyncGenerator
import uuid

# NOTE: DNS resolution and SSL handled by system - no custom patching needed

# Set Firebase API Key for OpenGradient SDK if provided
from config import OPENGRADIENT_FIREBASE_API_KEY
if OPENGRADIENT_FIREBASE_API_KEY:
    import opengradient.client.model_hub as model_hub
    if hasattr(model_hub, '_FIREBASE_CONFIG'):
        model_hub._FIREBASE_CONFIG = {"apiKey": OPENGRADIENT_FIREBASE_API_KEY}
else:
    # Set empty config to avoid Firebase errors
    import opengradient.client.model_hub as model_hub
    if hasattr(model_hub, '_FIREBASE_CONFIG'):
        model_hub._FIREBASE_CONFIG = {}

import opengradient as og
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage, SystemMessage, HumanMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from pydantic import BaseModel, Field

from config import OPENGRADIENT_PRIVATE_KEY
from scrapers.twitter_scraper import TwitterScraper
from scrapers.solana_scraper import SolanaScraper
from utils.cache import cache, CacheKeys, CacheTTL

logger = logging.getLogger(__name__)


def _resolve_settlement_mode(mode_enum: Any):
    """Pick SETTLE for real on-chain tx hash (requires OPG tokens).
    
    SETTLE ("private") - Returns real on-chain tx hash (0x...) ✅
    SETTLE_BATCH ("batch") - May work, batched settlement
    SETTLE_METADATA ("individual") - Returns 'external' ❌
    """
    # SETTLE = "private" → Real tx hash (0x...)
    if hasattr(mode_enum, "SETTLE"):
        return getattr(mode_enum, "SETTLE")
    # SETTLE_BATCH = "batch" → May work too
    if hasattr(mode_enum, "SETTLE_BATCH"):
        return getattr(mode_enum, "SETTLE_BATCH")
    # SETTLE_METADATA = "individual" → Returns "external" (NO real tx!)
    # Only use as last resort
    if hasattr(mode_enum, "SETTLE_METADATA"):
        return getattr(mode_enum, "SETTLE_METADATA")
    return None


def _pick_best_model_enum(og_module: Any, candidates: list[str]) -> str:
    """Return first model enum name available in current SDK."""
    tee_llm = getattr(og_module, "TEE_LLM", None)
    if tee_llm is None:
        return "GPT_4O"
    for name in candidates:
        if hasattr(tee_llm, name):
            return name
    return "GPT_4O"


def _normalize_llm_model_enum(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    model = str(value).strip().upper()
    return model or None


def _has_real_tx_hash(tx_hash: Any) -> bool:
    if not isinstance(tx_hash, str):
        return False
    tx = tx_hash.strip()
    if not tx or tx.lower() == "external":
        return False
    return tx.startswith("0x") and len(tx) >= 66

class SafeOpenGradientLLM(BaseChatModel):
    """Custom wrapper for OpenGradient synchronous client. Bypasses Windows aiohttp DNS bugs."""
    client: Any
    model_enum: str
    bound_tools: list = []

    def bind_tools(self, tools: list, **kwargs):
        """Mock bind_tools by storing tools to be passed as context or function calling schema if supported."""
        # For simplicity in this env, we just store them. 
        # A full implementation would map these to OpenAI-style tool schemas.
        self.bound_tools = tools
        return self
        
    def _generate(self, messages: list[BaseMessage], stop: Optional[list[str]] = None, run_manager=None, **kwargs) -> ChatResult:
        
        # 1. Format messages for OpenGradient
        formatted_msgs = []
        for m in messages:
            if isinstance(m, SystemMessage):
                formatted_msgs.append({"role": "system", "content": str(m.content)})
            elif isinstance(m, HumanMessage):
                formatted_msgs.append({"role": "user", "content": str(m.content)})
            elif isinstance(m, AIMessage):
                # If it's a tool call, we encode it as assistant reasoning
                content = str(m.content) if m.content else ""
                if hasattr(m, "tool_calls") and m.tool_calls:
                    content += "\n[ACTION REQUEST: " + json.dumps(m.tool_calls) + "]"
                formatted_msgs.append({"role": "assistant", "content": content})
            elif isinstance(m, ToolMessage):
                # Format tool output as user message so the LLM sees it
                formatted_msgs.append({"role": "user", "content": f"Tool '{m.name}' Returned: {m.content}"})

        # 2. Append tool schemas to the system prompt if we have tools
        if self.bound_tools and len(formatted_msgs) > 0 and formatted_msgs[0]["role"] == "system":
            tool_desc = "\n\nAVAILABLE TOOLS (To use a tool, output a JSON object like: {\"tool\": \"fetch_latest_tweets\", \"args\": {\"query\": \"$PENGUIN\"}}):\n"
            for t in self.bound_tools:
                tool_desc += f"- {t.name}: {t.description}\n"
            formatted_msgs[0]["content"] += tool_desc

        # 3. Call SDK synchronously
        try:
            settlement_mode = _resolve_settlement_mode(og.x402SettlementMode)
            res = getattr(self.client, "llm").chat(
                model=getattr(og.TEE_LLM, self.model_enum),
                messages=formatted_msgs,
                max_tokens=800,
                temperature=0.3,
                x402_settlement_mode=settlement_mode or og.x402SettlementMode.SETTLE_METADATA,
            )
        except Exception as e:
            logger_err = f"TEE SDK Error: {e}"
            logger.error(logger_err)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

        logger.info(f"OpenGradient raw response object: {res}")
        logger.info(f"OpenGradient chat_output: {res.chat_output}")

        if isinstance(res.chat_output, str):
            content = res.chat_output
        elif isinstance(res.chat_output, dict):
            content = res.chat_output.get("content", "")
        else:
            content = str(res.chat_output)
            
        tx_hash = getattr(res, "transaction_hash", None)
        
        # 4. Parse if the LLM attempted to call a tool via raw JSON
        tool_calls = []
        if '{"tool":' in content:
            try:
                # Naive json parsing for the tool call
                start = content.find('{"tool":')
                end = content.find('}', start) + 1
                if start >= 0 and end > start:
                    tool_json = json.loads(content[start:end])
                    # LangGraph format: [{'name': str, 'args': dict, 'id': str}]
                    tool_calls.append({
                        "name": tool_json["tool"],
                        "args": tool_json.get("args", {}),
                        "id": str(uuid.uuid4())
                    })
                    # Strip the tool JSON from visible content
                    content = content[:start].strip()
            except Exception as e:
                logger.warning(f"Failed to parse tool call from content: {e}")

        msg = AIMessage(content=content, tool_calls=tool_calls)
        gen = ChatGeneration(message=msg, generation_info={"tx_hash": tx_hash})
        return ChatResult(generations=[gen])

    @property
    def _llm_type(self) -> str:
        return "opengradient_safe"

try:
    from coincheckgo.utils.memsync import MemSyncClient
except ImportError:
    from utils.memsync import MemSyncClient
from concurrent.futures import ThreadPoolExecutor
import uuid


def _resolve_private_key() -> str | None:
    """
    Resolve the OpenGradient private key from multiple sources:
    1. .env / environment variable (OPENGRADIENT_PRIVATE_KEY or OG_PRIVATE_KEY)
    2. ~/.opengradient_config.json (created by `opengradient config init`)
    """
    if OPENGRADIENT_PRIVATE_KEY and "YOUR" not in OPENGRADIENT_PRIVATE_KEY:
        return OPENGRADIENT_PRIVATE_KEY

    # Fallback: read from opengradient CLI config
    config_path = Path.home() / ".opengradient_config.json"
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text())
            key = data.get("private_key", "")
            if key:
                # Ensure 0x prefix
                if not key.startswith("0x"):
                    key = f"0x{key}"
                logger.info("Using private key from ~/.opengradient_config.json")
                return key
        except Exception:
            pass

    return None


# ═══ System Prompt ═══════════════════════════════════════════════════════════
# This is sent to the LLM inside TEE — it becomes part of the verifiable
# inference attestation and cannot be tampered with after execution.

SYSTEM_PROMPT = """\
You are 'CoinCheckGo Sentinel' — a ruthless, data-driven meme coin auditor on Solana.
Your analysis is verified on-chain via OpenGradient TEE — your output is immutable proof.

ROLE: You are the PRIMARY SCORING AUTHORITY. The algorithmic pre-scores are provided as
reference data, but YOU make the final trust assessment based on ALL evidence.

BEGINNER-FIRST STYLE (CRITICAL):
- Write for a complete beginner.
- Use simple words. No jargon. No slang. No memes.
- No emojis.
- Be explicit about what is known vs unknown.
- Always include a clear safety warning in the verdict (NOT financial advice; high-volatility; you can lose 100%).

CRITICAL TOKEN CLASSIFICATION:
Before scoring, classify the token type based on the provided "Token Profile":

1. **ESTABLISHED LARGE-CAP MEME** (is_large_cap=true AND is_established=true):
   - Examples: BONK, WIF, POPCAT, PENGU, FARTCOIN
   - These have survived market cycles, have 50K+ holders, $1M+ liquidity
   - Scoring approach: Start from 60 trust. Focus on momentum, community health, whale behavior.
   - OVERRIDE RULES RELAXED: Mint authority less critical if token is >6 months old with diverse holders

2. **ESTABLISHED MID-CAP** (is_established=true but not large_cap):
   - 10K-50K holders, 30+ days old, decent liquidity
   - Scoring approach: Start from 45 trust. Balanced assessment.

3. **NEW/EMERGING TOKEN** (is_established=false):
   - < 10K holders OR < 30 days old
   - Scoring approach: Start from 25 trust. BE BRUTAL. Most are rugs.
   - STRICT RULES: Mint authority MUST be renounced, need proof of organic community

4. **SUSPECT/LOW LIQUIDITY** (liquidity <$50K AND holders <1000):
   - Scoring approach: Start from 15 trust. EXTREME scrutiny.
   - Require ALL green flags to exceed 40 trust.

SCORING GUIDE for ai_trust_score:

ESTABLISHED LARGE-CAP MEME (is_large_cap + is_established):
- 0-30   EXTREME RISK: Active mint authority + whale dominance + bot army despite age
- 31-50  HIGH RISK: Smart money selling heavily, community dying, technical breakdown
- 51-65  MODERATE: Mixed signals but fundamentals okay (typical for most established memes)
- 66-80  LOW RISK: Healthy community, smart money accumulating, good momentum
- 81-95  STRONG: Exceptional metrics, viral momentum, whale accumulation (true moonshot)
- 96-100 LEGENDARY: BONK/WIF-level established community with perfect on-chain metrics

ESTABLISHED MID-CAP (is_established only):
- 0-25   EXTREME RISK: Active authorities + bot-dominated + smart money exodus
- 26-45  HIGH RISK: Multiple red flags, fading community
- 46-60  MEDIUM: Mixed signals, needs more research
- 61-75  LOW RISK: Decent fundamentals, organic growth visible
- 76-90  STRONG: Great metrics, trending positively

NEW/EMERGING (not established):
- 0-20   EXTREME RISK: Classic rug setup (mint active, 100% bots, 90%+ whales)
- 21-35  HIGH RISK: Multiple red flags, likely soft rug
- 36-50  MEDIUM RISK: Could be real but DYOR heavily
- 51-70  LOW RISK FOR NEW: Shows promise - organic tweets, renounced, good distribution
- 71-85  STRONG FOR NEW: Exceptional for a new token - rare, verify twice

MANDATORY RED FLAG CHECKLIST (adjust severity by token type):
- Mint authority NOT renounced (on NEW tokens = critical; on 6mo+ established = note but don't cap)
- Freeze authority active (always note, but less critical for established)
- Top 10 holders own >50% supply (critical for new, concerning for established)
- Top 10 holders own >70% supply (always cap trust at 50 max regardless of age)
- Smart money count < 3 → "No Smart Money Interest - Only X wallets"
- Smart money netflow negative >$100K → "Smart Money Selling - Net outflow"
- Bot score > 40 → "Bot Activity Elevated - X% synthetic engagement"
- Bot score > 70 → "Bot Army - Likely manipulated social metrics"
- Social score < 30 (on established) → "Declining Community Interest"
- Social score < 10 (on new) → "No Real Community - Ghost town"
- No real tweets available → "No Community Data - Unable to verify social legitimacy"
- Holder concentration > 60% in single wallet → "Single Whale Control - Massive risk"
- Token < 24h old → "Just Launched - Extreme volatility expected"

MANDATORY GREEN FLAG CHECKLIST (must have evidence):
- Mint authority renounced → "Mint Renounced - Supply capped, no infinite mint"
- No freeze authority → "No Freeze - Funds cannot be locked"
- Diverse holder distribution (top10 <30%) → "Decentralized Holders - No whale cartel"
- Smart money netflow positive >$50K → "Smart Money Buying - X wallets accumulating"
- Organic tweet engagement (unique authors >20, diverse content) → "Real Community - Authentic X discussions"
- Low bot percentage (<20%) → "Organic Engagement - Minimal bot interference"
- Holder score > 70 → "Healthy Distribution - Broad token ownership"
- 50K+ holders (established) → "Large Community - Institutional-grade holder base"
- 6+ months old with active community → "Survived Cycles - Battle-tested meme"

OUTPUT FORMAT - You MUST output valid JSON with this EXACT structure:
{
  "ai_trust_score": <integer 0-100>,
  "ai_sentiment_score": <integer 0-100>,
  "verdict": "<2-4 short sentences. MUST be beginner-friendly. Include: (1) plain summary of risk, (2) what is good, (3) what is risky, (4) what a newbie should do next (avoid/wait/DYOR). MUST include a safety warning: 'Not financial advice' and 'you can lose 100%'.>",
  "red_flags": ["<Risk Factors (Red Flags). Each item MUST be 1 short sentence with NUMBERS + why it matters. Format: '<Issue with evidence> — <why it matters in plain words>'. Example: 'Top 10 holders own 67% of supply — a few wallets can dump and crash the price.'>"],
  "green_flags": ["<Positive Signals (Green Flags). Each item MUST be 1 short sentence with NUMBERS + why it matters. Format: '<Signal with evidence> — <why it matters in plain words>'. Example: 'Mint authority renounced — supply cannot be inflated by printing more tokens.'>"],
  "bot_likelihood": "<NONE | LOW | MEDIUM | HIGH | EXTREME>",
  "recommendation": "<AVOID | RISKY | DYOR | LOOKS_OK | BULLISH>",
  "scoring_rationale": "<2-3 short sentences using simple arithmetic and plain words. Example: 'Base 45 for established token, -20 for whale concentration, +5 for good liquidity, final 30'>",
  "twitter_real": <boolean true if you successfully extracted real tweets using your tool, false if not>,
  "onchain_real": <boolean true if you successfully analyzed real on-chain data, false if not>
}

RULES:
1. READ the Token Profile first - this determines your starting trust baseline.
2. For LARGE-CAP ESTABLISHED tokens (50K+ holders, 6+ months old): Be fair. Don't penalize heavily for minor issues. These have proven staying power.
3. For NEW tokens: Be BRUTAL. Require PROOF of legitimacy. Most new meme coins are rugs.
4. ALWAYS cite NUMBERS in red_flags and green_flags. Specific data points are required.
5. If twitter_real=false or onchain_real=false, add a red_flags item that explains what is missing and that the result is less reliable.
6. If a key risk metric is missing/unknown, treat it as a risk and put it in red_flags as "Unknown/Unavailable" with the consequence.
7. Keep lists short and readable: max 6 red_flags and max 6 green_flags.
8. Output ONLY the JSON object, nothing else before or after.
"""


class OpenGradientAnalyzer:
    """
    Wrapper for the OpenGradient SDK (v0.6.0+ — client.llm.chat API).
    Uses TEE-verified LLM inference for verifiable meme-coin analysis.

    The SDK handles x402 payment flow on Base Sepolia ($OPG) automatically.
    Permit2 approval is handled via client.llm.ensure_opg_approval().
    OPG = 18 decimals, fee ≈ 0.000001 OPG/request → 0.2 OPG = millions of requests.

    Prerequisites:
        1. pip install opengradient
        2. opengradient config init
        3. Fund wallet with $OPG from https://faucet.opengradient.ai
    """

    # Model fallback chain — try cheaper/faster models if main one fails
    # Note: GPT_4_1_2025_04_14 is the primary supported model for x402 payments
    MODEL_FALLBACK = [
        "CLAUDE_4_0_SONNET",
        "GPT_4_1_2025_04_14",
        "GPT_4O",
        "CLAUDE_3_5_HAIKU",
    ]
    REQUIRE_X402_TX = True  # Re-enable for production

    def __init__(self):
        self._client = None
        self._og = None
        self._memsync = MemSyncClient()
        self._initialized = False
        self._init_error = None

        private_key = _resolve_private_key()
        if not private_key:
            self._init_error = "OPENGRADIENT_PRIVATE_KEY not set"
            logger.warning(f"❌ OpenGradient init failed: {self._init_error}")
            return

        try:
            self._og = og

            self._client = og.Client(private_key=private_key)
            self._initialized = True
            
            # Ensure OPG approval before inference
            try:
                logger.info("🔍 Checking OPG approval...")
                
                # Use client.llm.ensure_opg_approval directly (preferred method)
                try:
                    logger.info("🔍 Checking OPG approval via client.llm...")
                    approval = self._client.llm.ensure_opg_approval(opg_amount=5.0)
                    if approval.tx_hash:
                        logger.info(f"✅ [PERMIT2] Approval TX: {approval.tx_hash}")
                    else:
                        logger.info(f"✅ [PERMIT2] Already approved: {approval.allowance_before}")
                except Exception as e:
                    logger.warning(f"⚠️ Permit2 approval failed: {e}")
                    
            except Exception as e:
                logger.warning(f"⚠️ Permit2 approval failed: {e}")

            self._selected_model = _pick_best_model_enum(self._og, self.MODEL_FALLBACK)
            
            # Init LLM via custom safe adapter
            self._llm = SafeOpenGradientLLM(client=self._client, model_enum=self._selected_model)
            
            # Init tools
            self._solana_scraper = SolanaScraper()
            self._twitter_scraper = TwitterScraper()

            logger.info(
                f"✅ OpenGradient SDK initialized — key: "
                f"{private_key[:6]}...{private_key[-4:]}"
            )
            logger.info(f"OpenGradient model selected: {self._selected_model}")
        except Exception as e:
            self._init_error = str(e)
            logger.error(f"❌ OpenGradient SDK init failed: {e}")

    def _submit_alpha_signal(self, ticker: str, ai_result: dict) -> Optional[str]:
        """
        Submit a lightweight inference task to the Alpha Testnet to verify the analysis on-chain.
        Uses the 'intfloat/multilingual-e5-large-instruct' embedding model.
        """
        try:
            # Construct a verification query based on the analysis
            verdict = ai_result.get("verdict", "Analysis pending")
            query = f"Verify analysis for ${ticker}: {verdict}"
            
            logger.info(f"Submitting Alpha Signal for {ticker}...")
            
            # Using alpha.infer with retry logic
            if hasattr(self._client, "alpha"):
                for attempt in range(1, 4):
                    try:
                        result = self._client.alpha.infer(
                            model_cid="intfloat/multilingual-e5-large-instruct",
                            model_input={
                                "queries": [query],
                                "instruction": ["Verify the sentiment analysis"],
                                "passages": [json.dumps(ai_result)]
                            },
                            inference_mode=og.InferenceMode.VANILLA
                        )
                        if result.transaction_hash:
                            return result.transaction_hash
                    except Exception as try_err:
                        logger.warning(f"Alpha Signal attempt {attempt} failed: {try_err}")
                        continue
                return None
            else:
                logger.warning("SDK does not support alpha namespace")
                return None
            
        except Exception as e:
            logger.warning(f"Alpha Signal submission failed: {e}")
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    async def analyze_coin(
        self,
        ticker: str,
        tweets_summary: list[dict],
        on_chain_data: dict,
        scoring_result: dict,
        llm_model: Optional[str] = None,
    ) -> dict:
        """
        Run verifiable AI inference on OpenGradient.
        
        Steps:
        1. Check cache for existing AI verdict
        2. Run OpenGradient TEE inference if not cached
        3. Cache result for 30 minutes
        """
        # Check cache first
        requested_model = _normalize_llm_model_enum(llm_model)
        selected_model = self._selected_model
        if requested_model:
            selected_model = _pick_best_model_enum(self._og, [requested_model] + self.MODEL_FALLBACK)

        cache_key = CacheKeys.ai_verdict(ticker, selected_model)
        cached_result = await cache.get(cache_key)
        if cached_result:
            logger.info(f"AI verdict cache HIT for {ticker}")
            return cached_result
        
        # Run Inference
        result = await self._run_inference(
            ticker,
            tweets_summary,
            on_chain_data,
            scoring_result,
            llm_model=selected_model,
        )

        # Cache successful result
        if result.get("ai_result"):
            await cache.set(cache_key, result, CacheTTL.AI_VERDICT)
            logger.info(f"AI verdict cached for {ticker} (TTL: {CacheTTL.AI_VERDICT}s)")

        return result

    async def analyze_coin_stream(
        self,
        ticker: str,
        tweets_summary: list[dict],
        on_chain_data: dict,
        scoring_result: dict,
        llm_model: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        requested_model = _normalize_llm_model_enum(llm_model)
        selected_model = self._selected_model
        if requested_model:
            selected_model = _pick_best_model_enum(self._og, [requested_model] + self.MODEL_FALLBACK)

        cache_key = CacheKeys.ai_verdict(ticker, selected_model)
        cached_result = await cache.get(cache_key)
        if cached_result:
            logger.info(f"AI verdict cache HIT for {ticker}")
            yield {"type": "done", "result": cached_result}
            return

        input_text = self._prepare_input(ticker, tweets_summary, on_chain_data, scoring_result)
        algo_score = scoring_result.get("overall_score", 50)

        if not self._initialized:
            error_msg = self._init_error or "OpenGradient SDK not initialized"
            logger.error(f"❌ OpenGradient failed: {error_msg}")
            yield {
                "type": "done",
                "result": {
                    "ai_result": None,
                    "tx_hash": None,
                    "transaction_hash": None,
                    "model_cid": None,
                    "raw_output": None,
                    "used_opengradient": False,
                    "error": error_msg,
                },
            }
            return

        def _as_float(value, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        holder_count = (
            int(_as_float(on_chain_data.get("holders", {}).get("total_holders", 0), 0.0)) if on_chain_data else 0
        )
        token_age_hours = (
            _as_float(on_chain_data.get("token_info", {}).get("age_hours", 0), 0.0) if on_chain_data else 0.0
        )
        liquidity_usd = _as_float(on_chain_data.get("liquidity_usd", 0), 0.0) if on_chain_data else 0.0

        is_established = holder_count > 10000 or token_age_hours > 720
        is_large_cap = liquidity_usd > 1000000 or holder_count > 50000
        if is_large_cap:
            token_type = "large_cap"
        elif is_established:
            token_type = "established"
        else:
            token_type = "new"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": input_text},
        ]

        model_cid = getattr(og.TEE_LLM, selected_model, og.TEE_LLM.GPT_4O)
        settlement_mode = _resolve_settlement_mode(og.x402SettlementMode)

        queue: asyncio.Queue[dict] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _worker() -> None:
            raw_parts: list[str] = []
            tx_hash: Optional[str] = None
            payment_hash: Optional[str] = None
            try:
                stream = self._client.llm.chat(
                    model=model_cid,
                    messages=messages,
                    max_tokens=2000,
                    temperature=0.1,
                    x402_settlement_mode=settlement_mode,
                    stream=True,
                )

                for chunk in stream:
                    try:
                        choices = getattr(chunk, "choices", None)
                        if not choices:
                            continue
                        delta = getattr(choices[0], "delta", None)
                        content = getattr(delta, "content", None) if delta is not None else None
                    except Exception:
                        continue

                    if not content or not isinstance(content, str):
                        continue

                    if content.startswith("__OG_META__"):
                        try:
                            meta = json.loads(content[len("__OG_META__") :])
                            tx_hash = meta.get("tx_hash") or tx_hash
                            payment_hash = meta.get("payment_hash") or payment_hash
                            loop.call_soon_threadsafe(
                                queue.put_nowait,
                                {
                                    "type": "meta",
                                    "tx_hash": tx_hash,
                                    "payment_hash": payment_hash,
                                },
                            )
                        except Exception:
                            continue
                        continue

                    raw_parts.append(content)

            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "error": str(e)})
            finally:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "type": "stream_done",
                        "raw_output": "".join(raw_parts),
                        "tx_hash": tx_hash,
                        "payment_hash": payment_hash,
                    },
                )

        threading.Thread(target=_worker, daemon=True).start()

        stream_error: Optional[str] = None
        raw_output: str = ""
        tx_hash: Optional[str] = None

        while True:
            event = await queue.get()
            if event.get("type") == "meta":
                yield event
                continue
            if event.get("type") == "error":
                stream_error = event.get("error")
                continue
            if event.get("type") == "stream_done":
                raw_output = event.get("raw_output") or ""
                tx_hash = event.get("tx_hash")
                break

        if stream_error:
            logger.warning(f"[METHOD A STREAM] Failed: {stream_error}")
            yield {
                "type": "done",
                "result": {
                    "ai_result": None,
                    "tx_hash": None,
                    "transaction_hash": None,
                    "model_cid": None,
                    "raw_output": raw_output,
                    "model_used": None,
                    "used_opengradient": False,
                    "error": stream_error,
                },
            }
            return

        if self.REQUIRE_X402_TX and tx_hash and not _has_real_tx_hash(tx_hash):
            err = f"x402 tx hash required but got: {tx_hash}"
            logger.warning(f"[METHOD A STREAM] {err}")
            yield {
                "type": "done",
                "result": {
                    "ai_result": None,
                    "tx_hash": None,
                    "transaction_hash": tx_hash,
                    "model_cid": selected_model,
                    "raw_output": raw_output,
                    "model_used": f"Direct LLM.chat(stream=True)/{selected_model}",
                    "used_opengradient": False,
                    "error": err,
                },
            }
            return

        if self.REQUIRE_X402_TX and not tx_hash:
            logger.warning(
                "[METHOD A STREAM] Missing x402 tx hash (tx_hash=None). Continuing with AI output but verification will be unavailable."
            )

        ai_result = self._parse_ai_output(raw_output, token_type, algo_score)
        required_fields = ["ai_trust_score", "ai_sentiment_score", "verdict", "red_flags", "green_flags"]
        missing_fields = [f for f in required_fields if f not in ai_result]

        if missing_fields:
            logger.warning(f"[METHOD A STREAM] Missing fields: {missing_fields}")
            yield {
                "type": "done",
                "result": {
                    "ai_result": self._default_result(token_type, f"Missing fields: {missing_fields}", algo_score),
                    "tx_hash": tx_hash if _has_real_tx_hash(tx_hash) else None,
                    "transaction_hash": tx_hash,
                    "model_cid": selected_model,
                    "raw_output": raw_output,
                    "model_used": f"Direct LLM.chat(stream=True)/{selected_model}",
                    "used_opengradient": True,
                },
            }
            return

        result = {
            "ai_result": ai_result,
            "tx_hash": tx_hash if _has_real_tx_hash(tx_hash) else None,
            "transaction_hash": tx_hash,
            "model_cid": selected_model,
            "raw_output": raw_output,
            "model_used": f"Direct LLM.chat(stream=True)/{selected_model}",
            "used_opengradient": True,
        }

        if result.get("ai_result"):
            await cache.set(cache_key, result, CacheTTL.AI_VERDICT)
            logger.info(f"AI verdict cached for {ticker} (TTL: {CacheTTL.AI_VERDICT}s)")

        yield {"type": "done", "result": result}

    async def _run_inference(
        self,
        ticker: str,
        tweets_summary: list[dict],
        on_chain_data: dict,
        scoring_result: dict,
        extra_context: str = "",
        llm_model: Optional[str] = None,
    ) -> dict:
        """
        Run AI inference with dual approach:
        A) Direct client.llm.chat() - simpler, gets tx_hash directly
        B) LangChain ReAct agent - fallback with tool support
        """
        input_text = self._prepare_input(
            ticker, tweets_summary, on_chain_data, scoring_result, extra_context
        )
        
        algo_score = scoring_result.get("overall_score", 50)

        if not self._initialized:
            error_msg = self._init_error or "OpenGradient SDK not initialized"
            logger.error(f"❌ OpenGradient failed: {error_msg}")
            return {
                "ai_result": None,
                "tx_hash": None,
                "transaction_hash": None,
                "model_cid": None,
                "raw_output": None,
                "used_opengradient": False,
                "error": error_msg,
            }

        # Calculate token type for fallback handling
        def _as_float(value, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        holder_count = int(_as_float(on_chain_data.get("holders", {}).get("total_holders", 0), 0.0)) if on_chain_data else 0
        token_age_hours = _as_float(on_chain_data.get("token_info", {}).get("age_hours", 0), 0.0) if on_chain_data else 0.0
        liquidity_usd = _as_float(on_chain_data.get("liquidity_usd", 0), 0.0) if on_chain_data else 0.0
        
        is_established = holder_count > 10000 or token_age_hours > 720
        is_large_cap = liquidity_usd > 1000000 or holder_count > 50000
        
        if is_large_cap:
            token_type = "large_cap"
        elif is_established:
            token_type = "established"
        else:
            token_type = "new"

        # ════════════════════════════════════════════════════════════════════════
        # METHOD A: Direct og.llm_chat() - NEW SDK API
        # ════════════════════════════════════════════════════════════════════════
        try:
            logger.info(f"🧠 [METHOD A] Running og.llm_chat()...")

            requested_model = _normalize_llm_model_enum(llm_model)
            selected_model = self._selected_model
            if requested_model:
                selected_model = _pick_best_model_enum(self._og, [requested_model] + self.MODEL_FALLBACK)
            
            # Build messages for direct chat
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": input_text}
            ]
            
            # Use TEE_LLM from SDK
            model_cid = getattr(og.TEE_LLM, selected_model, og.TEE_LLM.GPT_4O)
            
            # NEW SDK API: Use client.llm.chat() instead of og.llm_chat()
            # Use SETTLE mode for real tx hash
            settlement_mode = _resolve_settlement_mode(og.x402SettlementMode)
            
            completion = self._client.llm.chat(
                model=model_cid,
                messages=messages,
                max_tokens=2000,
                temperature=0.1,
                x402_settlement_mode=settlement_mode,
            )
            
            # Extract from completion object
            tx_hash = getattr(completion, 'transaction_hash', None)
            finish_reason = getattr(completion, 'finish_reason', None)
            chat_output = getattr(completion, 'chat_output', None)
            
            # Extract content from SDK chat_output
            # New SDK returns TextGenerationOutput(chat_output=<dict|str>, transaction_hash=...)
            if isinstance(chat_output, dict):
                raw_output = chat_output.get("content", "")
            else:
                raw_output = str(chat_output) if chat_output else ""
            
            # Log what we got for debugging
            logger.info(f"🔍 [METHOD A] Got TX: {tx_hash} (type: {type(tx_hash)})")
            logger.info(f"🔍 [METHOD A] Settlement mode used: {settlement_mode}")
            
            if self.REQUIRE_X402_TX and tx_hash and tx_hash != "external" and not _has_real_tx_hash(tx_hash):
                raise RuntimeError(f"x402 tx hash required but got: {tx_hash}")
            
            logger.info(f"✅ [METHOD A] Direct LLM success - TX: {tx_hash}, finish: {finish_reason}")
            logger.info(f"📝 Raw output: {raw_output[:300]}...")
            
            if raw_output:
                # Parse and return
                ai_result = self._parse_ai_output(raw_output, token_type, algo_score)
                
                # Validate required fields
                required_fields = ['ai_trust_score', 'ai_sentiment_score', 'verdict', 'red_flags', 'green_flags']
                missing_fields = [f for f in required_fields if f not in ai_result]
                
                if not missing_fields:
                    logger.info(f"✅ [OPENGRADIENT] Method A Success - trust={ai_result.get('ai_trust_score')}")
                    return {
                        "ai_result": ai_result,
                        "tx_hash": tx_hash if _has_real_tx_hash(tx_hash) else None,
                        "transaction_hash": tx_hash,
                        "model_cid": selected_model,
                        "raw_output": raw_output,
                        "model_used": f"Direct LLM.chat()/{selected_model}",
                        "used_opengradient": True,
                    }
                else:
                    logger.warning(f"[METHOD A] Missing fields: {missing_fields}, trying fallback...")
            else:
                logger.warning("[METHOD A] Empty output, trying fallback...")
                
        except Exception as e:
            logger.warning(f"[METHOD A] Failed: {e}, trying fallback...")

        # ════════════════════════════════════════════════════════════════════════
        # METHOD B: LangChain ReAct Agent - Fallback approach
        # ════════════════════════════════════════════════════════════════════════
        try:
            logger.info(f"🤖 [METHOD B] Falling back to LangChain ReAct Agent...")
            
            # Prepare Tools (Twitter removed for Vercel compatibility)
            tools = []
            
            # Initialize Agent
            requested_model = _normalize_llm_model_enum(llm_model)
            selected_model = self._selected_model
            if requested_model:
                selected_model = _pick_best_model_enum(self._og, [requested_model] + self.MODEL_FALLBACK)
            llm = SafeOpenGradientLLM(client=self._client, model_enum=selected_model)

            agent = create_react_agent(llm, tools)
            
            # Run Agent
            final_state = await agent.ainvoke(
                {"messages": [("system", SYSTEM_PROMPT), ("user", input_text)]},
                {"configurable": {"thread_id": str(uuid.uuid4())}}
            )
            
            if not final_state.get("messages"):
                raise Exception("Agent returned no messages")
            
            raw_output = final_state["messages"][-1].content
            logger.info(f"📝 [METHOD B] Raw output: {raw_output[:300]}...")
            
            # Parse output
            ai_result = self._parse_ai_output(raw_output, token_type, algo_score)
            
            # Validate
            required_fields = ['ai_trust_score', 'ai_sentiment_score', 'verdict', 'red_flags', 'green_flags']
            missing_fields = [f for f in required_fields if f not in ai_result]
            
            if missing_fields:
                logger.warning(f"[METHOD B] Missing fields: {missing_fields}")
                return {
                    "ai_result": self._default_result(token_type, f"Missing fields: {missing_fields}", algo_score),
                    "tx_hash": None,
                    "transaction_hash": None,
                    "model_cid": None,
                    "raw_output": raw_output,
                    "model_used": "LangChain ReAct (fallback)",
                    "used_opengradient": True,
                }

            logger.info(f"✅ [OPENGRADIENT] Method B Success - trust={ai_result.get('ai_trust_score')}")
            return {
                "ai_result": ai_result,
                "tx_hash": None,  # LangChain adapter doesn't expose tx_hash easily
                "transaction_hash": None,
                "model_cid": None,
                "raw_output": raw_output,
                "model_used": "LangChain ReAct",
                "used_opengradient": True,
            }
        
        except Exception as e:
            logger.error(f"[METHOD B] Also failed: {e}")

        # All methods failed
        error_msg = str(e)
        if "402" in error_msg or "payment" in error_msg.lower():
            error_msg = (
                f"Wallet needs $OPG testnet tokens on Base Sepolia. "
                f"Get tokens from https://faucet.opengradient.ai/ then retry. "
                f"Original error: {e}"
            )

        logger.error(f"All OpenGradient methods failed: {error_msg}")
        return {
            "ai_result": None,
            "tx_hash": None,
            "transaction_hash": None,
            "model_cid": None,
            "raw_output": None,
            "model_used": None,
            "used_opengradient": False,
            "error": error_msg,
        }

    # ── Input Preparation ─────────────────────────────────────────────────────

    def _prepare_input(
        self,
        ticker: str,
        tweets: list[dict],
        on_chain: dict,
        scores: dict,
        extra_context: str = "",
    ) -> str:
        """Build input text for the LLM. Includes token classification for nuanced scoring."""

        def _as_float(value, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
        
        # Calculate token classification for AI context
        holder_data = on_chain.get("holders", {}) if on_chain else {}
        token_info = on_chain.get("token_info", {}) if on_chain else {}
        market_data = on_chain.get("market_data", {}) if on_chain else {}
        
        holder_count = int(_as_float(holder_data.get("total_holders", 0), 0.0))
        if holder_count <= 0:
            holder_count = int(_as_float(market_data.get("holders", 0), 0.0))
        token_age_hours = _as_float(token_info.get("age_hours", 0), 0.0)
        liquidity_usd = _as_float(on_chain.get("liquidity_usd", 0), 0.0) if on_chain else 0.0
        
        # Classification thresholds (must match routes.py)
        is_established = holder_count > 10000 or token_age_hours > 720  # >10K holders or >30 days
        is_large_cap = liquidity_usd > 1000000 or holder_count > 50000  # >$1M liquidity or >50K holders
        
        # Determine token type label
        if is_large_cap:
            token_type = "ESTABLISHED LARGE-CAP MEME (BONK/WIF tier)"
        elif is_established:
            token_type = "ESTABLISHED MID-CAP"
        elif holder_count < 1000 and liquidity_usd < 50000:
            token_type = "SUSPECT/LOW LIQUIDITY"
        else:
            token_type = "NEW/EMERGING TOKEN"
        
        parts = [
            f"=== ANALYSIS REQUEST: ${ticker} (Solana) ===\n",
        ]
        
        # TOKEN PROFILE - CRITICAL for AI scoring approach
        parts.append("=== TOKEN PROFILE (DETERMINES SCORING BASELINE) ===")
        parts.append(f"Token Type: {token_type}")
        parts.append(f"is_large_cap: {is_large_cap} (50K+ holders OR $1M+ liquidity)")
        parts.append(f"is_established: {is_established} (10K+ holders OR 30+ days old)")
        parts.append(f"Total Holders: {holder_count:,}")
        parts.append(f"Token Age: {token_age_hours:.1f} hours ({token_age_hours/24:.1f} days)")
        parts.append(f"Liquidity: ${liquidity_usd:,.0f}")
        parts.append("")
        parts.append("SCORING GUIDANCE:")
        if is_large_cap:
            parts.append("- This is a LARGE-CAP established meme coin (like BONK, WIF, POPCAT)")
            parts.append("- Start trust score at 60 (not 30) - these have proven staying power")
            parts.append("- Minor issues are less critical for 6+ month old tokens with 50K+ holders")
            parts.append("- Focus on: community health, smart money flow, momentum trends")
        elif is_established:
            parts.append("- This is an ESTABLISHED mid-cap token (10K+ holders or 30+ days)")
            parts.append("- Start trust score at 45 - reasonable track record")
            parts.append("- Balanced assessment: check for growth but don't be overly harsh")
        else:
            parts.append("- This is a NEW/EMERGING token (< 10K holders AND < 30 days)")
            parts.append("- Start trust score at 25 - BE BRUTAL, most are rugs")
            parts.append("- REQUIRE proof: mint renounced, organic tweets, good distribution")
        parts.append("")

        # On-chain data - CRITICAL for risk assessment
        if on_chain:
            parts.append("--- ON-CHAIN RISK FACTORS ---")
            if on_chain.get("token_info"):
                ti = on_chain["token_info"]
                mint_renounced = ti.get('is_mint_renounced')
                freeze_auth = ti.get('freeze_authority')
                age_hours = ti.get('age_hours')
                
                # Explicitly note missing data vs actual values
                if mint_renounced is not None:
                    parts.append(f"Mint Authority Renounced: {mint_renounced}")
                else:
                    parts.append("Mint Authority Renounced: DATA UNAVAILABLE - Assume HIGH RISK")
                    
                if freeze_auth is not None:
                    parts.append(f"Freeze Authority: {'None' if not freeze_auth else 'ACTIVE - HIGH RISK'}")
                else:
                    parts.append("Freeze Authority: DATA UNAVAILABLE - Assume HIGH RISK")
                    
                if age_hours is not None:
                    parts.append(f"Token Age: {age_hours} hours old")
                else:
                    parts.append("Token Age: DATA UNAVAILABLE")
            
            if on_chain.get("holders"):
                h = on_chain["holders"]
                top10 = h.get('top10_pct')
                largest = h.get('largest_holder_pct')
                total = h.get('total_holders')
                
                # Explicitly handle missing vs zero values
                if top10 is not None:
                    parts.append(
                        f"Top 10 Holders: {top10}% of supply (RISK if >50%)"
                    )
                else:
                    parts.append("Top 10 Holders: DATA UNAVAILABLE")
                    
                if largest is not None:
                    parts.append(
                        f"Top Wallet: {largest}% of supply (RISK if >80%)"
                    )
                else:
                    parts.append("Top Wallet: DATA UNAVAILABLE")
                    
                if total is not None:
                    parts.append(f"Total Holders: {total}")
                else:
                    parts.append("Total Holders: DATA UNAVAILABLE")

            if on_chain.get("market_data"):
                md = on_chain.get("market_data") or {}
                parts.append("")
                parts.append("--- MARKET DATA (BIRDEYE/DEX) ---")
                parts.append(f"Market Cap: {md.get('market_cap')}")
                parts.append(f"Liquidity (USD): {md.get('liquidity_usd')}")
                parts.append(f"Volume 24h (USD): {md.get('volume_24h')}")
                parts.append(f"Price Change 1h (%): {md.get('price_change_1h')}")
                parts.append(f"Price Change 24h (%): {md.get('price_change_24h')}")
                parts.append(f"Buy/Sell Ratio: {md.get('buy_sell_ratio')}")
                parts.append(f"Buy Volume %: {md.get('buy_volume_pct')}")

            # SMART MONEY DATA - NEW CRITICAL FACTOR
            if on_chain.get("smart_money"):
                sm = on_chain["smart_money"]
                parts.append(
                    f"Smart Money Wallets: {sm.get('smart_money_count', 0)} (GOOD if >5)"
                )
                parts.append(
                    f"Smart Money Netflow: {sm.get('smart_money_netflow', 0)} (NEGATIVE = SELLING)"
                )
                parts.append(
                    f"Smart Money Buy Volume: {sm.get('smart_money_buy_volume', 0)}"
                )
                parts.append(
                    f"Smart Money Sell Volume: {sm.get('smart_money_sell_volume', 0)}"
                )

        # Pre-computed scores
        parts.append("\n--- ALGORITHMIC SCORES (0-100) ---")
        parts.append(f"Holder Score: {scores.get('holder_score', '?')} (higher = better distribution)")
        parts.append(f"Liquidity Score: {scores.get('liquidity_score', '?')} (higher = better liquidity)")
        parts.append(f"Security Score: {scores.get('security_score', '?')} (higher = safer)")
        parts.append(f"Momentum Score: {scores.get('momentum_score', '?')} (price momentum)")
        parts.append(f"Volume Score: {scores.get('volume_score', '?')} (trading volume health)")

        # Tweet summary (limit to save tokens)
        if tweets:
            parts.append(f"\n--- COMMUNITY ANALYSIS ({len(tweets)} tweets) ---")
            for t in tweets[:8]:  # Reduced to save tokens
                username = t.get("author_username", "anon")
                text = (t.get("full_text", "") or "")[:150]  # Shorter text
                followers = t.get("author_followers", 0)
                likes = t.get("like_count", 0)
                parts.append(
                    f"@{username} ({followers}f, {likes}👍): {text}"
                )
        else:
            parts.append("\n--- SOCIAL DATA ---")
            parts.append("Social signals are disabled (no X/Twitter data).")

        return "\n".join(parts) + extra_context

    # ── Output Parsing ────────────────────────────────────────────────────────

    def _parse_ai_output(self, raw_output: str, token_type: str = "unknown", algo_score: int = 50) -> dict:
        """Parse the LLM JSON output. Returns default if parsing fails."""
        if not raw_output:
            logger.warning("Empty AI output, returning default result")
            return self._default_result(token_type, error_msg="Empty AI output", algo_score=algo_score)

        text = raw_output.strip()

        # Remove markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        # Find JSON object
        start = text.find("{")
        end = text.rfind("}") + 1

        if start >= 0 and end > start:
            try:
                result = json.loads(text[start:end])
                # Ensure required fields exist
                required_fields = ['ai_trust_score', 'verdict', 'red_flags', 'green_flags']
                for field in required_fields:
                    if field not in result:
                        logger.warning(f"AI output missing required field: {field}")
                        return self._default_result(token_type, error_msg=f"Missing required field: {field}", algo_score=algo_score)
                return result
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse AI JSON output: {e}")
                return self._default_result(token_type, error_msg=f"JSON Parse Error: {e}", algo_score=algo_score)

        return self._default_result(token_type, error_msg="No JSON object found in output", algo_score=algo_score)

    def _default_result(self, token_type: str = "unknown", error_msg: str = "", algo_score: int = 50) -> dict:
        """Return enhanced default result when AI parsing fails."""
        
        base_scores = {
            "large_cap": (65, f"Phân tích AI thất bại ({error_msg}). Meme coin vốn hóa lớn đã được khẳng định với cộng đồng vững chắc. Đang sử dụng điểm số cơ sở theo thuật toán an toàn."),
            "established": (50, f"Phân tích AI thất bại ({error_msg}). Token tầm trung đã được thiết lập với hồ sơ hợp lý. Đang sử dụng điểm số cơ sở theo thuật toán cân bằng."),
            "new": (30, f"Phân tích AI thất bại ({error_msg}). Token mới với lịch sử hạn chế. Đang sử dụng điểm số cơ sở theo thuật toán cẩn trọng do rủi ro rug pool cao."),
            "unknown": (45, f"Phân tích AI thất bại ({error_msg}). Chỉ sử dụng điểm số thuật toán. Tự nghiên cứu kỹ (DYOR) trước khi đầu tư."),
        }
        
        default_trust, verdict = base_scores.get(token_type, base_scores["unknown"])
        
        # Don't penalize the token too much if the algorithmic score is high, but the AI failed
        trust_score = max(default_trust, algo_score)
        
        return {
            "ai_trust_score": trust_score,
            "ai_sentiment_score": 50,
            "verdict": verdict,
            "red_flags": [f"Phân tích AI thất bại: {error_msg}"] if error_msg else ["Phân tích AI thất bại - chỉ dựa vào điểm số thuật toán"],
            "green_flags": [],
            "bot_likelihood": "UNKNOWN",
            "recommendation": "DYOR" if token_type in ["new", "unknown"] else "LOOKS_OK",
            "scoring_rationale": f"Fallback mặc định cho token {token_type} do lỗi AI inference. Điểm tin cậy được đặt ở {trust_score} dựa trên phân loại token và điểm thuật toán ({algo_score}). Lỗi: {error_msg}",
            "twitter_real": False,
            "onchain_real": False,
        }

