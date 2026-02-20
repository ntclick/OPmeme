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

import json
import logging
import os
from pathlib import Path
from typing import Optional

import opengradient as og

from config import OPENGRADIENT_PRIVATE_KEY

logger = logging.getLogger(__name__)

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

You MUST output valid JSON with this EXACT structure:
{
  "ai_trust_score": <integer 0-100>,
  "ai_sentiment_score": <integer 0-100>,
  "verdict": "<2-3 sentence assessment using crypto slang: FOMO, FUD, Jeet, Chad, Rug, Moon, Degen>",
  "red_flags": ["<list of specific concerns>"],
  "green_flags": ["<list of specific positives>"],
  "bot_likelihood": "<NONE | LOW | MEDIUM | HIGH | EXTREME>",
  "recommendation": "<AVOID | RISKY | DYOR | LOOKS_OK | BULLISH>",
  "scoring_rationale": "<1 sentence explaining your ai_trust_score>"
}

SCORING GUIDE for ai_trust_score:
- 0-20  EXTREME RISK: Confirmed scam signals (honeypot, dev rug, 100% bot tweets)
- 21-40 HIGH RISK: Multiple red flags (mint active, whale dominance, bot army)
- 41-60 MEDIUM RISK: Mixed signals, needs more research (DYOR territory)
- 61-80 LOW RISK: Decent project fundamentals, organic community
- 81-100 STRONG: Verified community, renounced mint, diverse holders (very rare for meme coins)

RULES:
1. Many identical/similar tweets → BOT ARMY → max trust 30.
2. KOLs shilling without product → PUMP_AND_DUMP → max trust 40.
3. Mint authority NOT renounced → ALWAYS flag, max trust 40.
4. Top holders own >70% → WHALE_RISK, max trust 35.
5. No real tweet data available → be conservative, max trust 50.
6. Be brutally honest. Never shill. Use humor but be accurate.
7. Output ONLY the JSON object, nothing else.
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
        "GPT_4_1_2025_04_14",
        "GPT_4O",
        "CLAUDE_3_5_HAIKU",
    ]

    def __init__(self):
        self._client = None
        self._og = None
        self._memsync = MemSyncClient()
        self._initialized = False

        private_key = _resolve_private_key()
        if not private_key:
            logger.warning(
                "OPENGRADIENT_PRIVATE_KEY/OG_PRIVATE_KEY not set and ~/.opengradient_config.json "
                "not found — AI fallback only. Run: opengradient config init"
            )
            return

        try:
            self._og = og
            self._client = og.Client(private_key=private_key)
            self._initialized = True

            # Ensure Permit2 has enough OPG allowance (SDK built-in method)
            # 18 decimals → fee per request ≈ 0.000001 OPG, so 5.0 is very generous
            try:
                if hasattr(self._client.llm, "ensure_opg_approval"):
                    approval = self._client.llm.ensure_opg_approval(opg_amount=5.0)
                    if approval.tx_hash:
                        logger.info(f"✅ [PERMIT2] Approval TX sent: {approval.tx_hash}")
                    else:
                        logger.info(
                            f"OPG Permit2 allowance: before={approval.allowance_before}, "
                            f"after={approval.allowance_after}"
                        )
                else:
                    logger.debug("SDK version does not support ensure_opg_approval (skipped)")
            except Exception as e:
                logger.warning(f"Permit2 approval check: {e} (may already be approved)")

            logger.info(
                f"OpenGradient SDK initialized — key: "
                f"{private_key[:6]}...{private_key[-4:]}"
            )
        except Exception as e:
            logger.warning(f"OpenGradient SDK not available (will use fallback): {e}")

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze_coin(
        self,
        ticker: str,
        tweets_summary: list[dict],
        on_chain_data: dict,
        scoring_result: dict,
    ) -> dict:
        """
        Run verifiable AI inference on OpenGradient.
        
        Steps:
        1. Run OpenGradient TEE inference.
        (MemSync disabled — API returning 422 errors)
        """
        # Run Inference (no MemSync context)
        memsync_context = ""
        result = self._run_inference(
            ticker, tweets_summary, on_chain_data, scoring_result, memsync_context
        )

        # MemSync store disabled (API returning 422 errors)
        
        return result

    def _run_inference(
        self,
        ticker: str,
        tweets_summary: list[dict],
        on_chain_data: dict,
        scoring_result: dict,
        extra_context: str = "",
    ) -> dict:
        input_text = self._prepare_input(
            ticker, tweets_summary, on_chain_data, scoring_result, extra_context
        )

        if not self._initialized:
            logger.warning("OpenGradient not initialized — returning fallback")
            return {
                "ai_result": self._fallback_analysis(scoring_result),
                "tx_hash": None,
                "model_cid": None,
                "raw_output": None,
                "used_opengradient": False,
                "error": "OpenGradient SDK not initialized",
            }

        # Try model fallback chain
        last_error = None

        for model_name in self.MODEL_FALLBACK:
            model_enum = getattr(og.TEE_LLM, model_name, None)
            if model_enum is None:
                continue

            try:
                logger.info(f"Calling OpenGradient LLM: {model_name}")

                # Run blocking SDK call in thread to avoid asyncio loop conflict
                def _chat_call():
                    return self._client.llm.chat(
                        model=model_enum,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": input_text},
                        ],
                        max_tokens=600,
                        temperature=0.3,
                        x402_settlement_mode=og.x402SettlementMode.SETTLE_METADATA,
                    )

                with ThreadPoolExecutor() as executor:
                    future = executor.submit(_chat_call)
                    result = future.result()

                raw_output = result.chat_output.get("content", "")
                tx_hash = getattr(result, "payment_hash", None)
                inference_tx = getattr(result, "transaction_hash", None)

                # Parse the JSON response
                ai_result = self._parse_ai_output(raw_output)

                logger.info(
                    f"✅ [OPENGRADIENT] Inference Success ({model_name})\n"
                    f"   ➤ Payment Hash:     {tx_hash}\n"
                    f"   ➤ Transaction Hash: {inference_tx}"
                )

                return {
                    "ai_result": ai_result,
                    "tx_hash": tx_hash,
                    "transaction_hash": inference_tx,
                    "model_cid": None,
                    "raw_output": raw_output,
                    "model_used": model_name,
                    "used_opengradient": True,
                }
            
            except Exception as e:
                last_error = e
                logger.warning(
                    f"OpenGradient {model_name} failed: {e}. "
                    f"Trying next model..."
                )
                continue

        # All models failed
        error_msg = str(last_error)
        if "402" in error_msg or "payment" in error_msg.lower():
            error_msg = (
                f"Wallet needs $OPG testnet tokens on Base Sepolia. "
                f"Get tokens from https://faucet.opengradient.ai/ then retry. "
                f"Original error: {last_error}"
            )

        logger.error(f"All OpenGradient models failed: {error_msg}")
        return {
            "ai_result": self._fallback_analysis(scoring_result),
            "tx_hash": None,
            "model_cid": None,
            "raw_output": None,
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
        """Build input text for the LLM. Keep it concise to reduce cost."""
        parts = [
            f"=== ANALYSIS REQUEST: ${ticker} (Solana) ===\n",
        ]

        # On-chain data
        if on_chain:
            parts.append("--- ON-CHAIN DATA ---")
            if on_chain.get("token_info"):
                ti = on_chain["token_info"]
                parts.append(
                    f"Mint Authority Renounced: {ti.get('is_mint_renounced', 'Unknown')}"
                )
                parts.append(
                    f"Freeze Authority: {'Active' if ti.get('freeze_authority') else 'None'}"
                )
            if on_chain.get("holders"):
                h = on_chain["holders"]
                parts.append(
                    f"Top 10 Holders: {h.get('top10_pct', '?')}% of supply"
                )
                parts.append(
                    f"Top Wallet: {h.get('top1_pct', '?')}% of supply"
                )

        # Pre-computed scores
        parts.append("\n--- PRE-COMPUTED SCORES (0-100) ---")
        parts.append(f"Social Score: {scores.get('social_score', '?')}")
        parts.append(f"Bot Score: {scores.get('bot_score', '?')} (higher = fewer bots)")
        parts.append(f"Holder Score: {scores.get('holder_score', '?')}")

        # Tweet summary (limit to save tokens)
        if tweets:
            parts.append(f"\n--- SAMPLE TWEETS ({len(tweets)} total) ---")
            for t in tweets[:10]:
                username = t.get("author_username", "anon")
                text = (t.get("full_text", "") or "")[:200]
                followers = t.get("author_followers", 0)
                likes = t.get("like_count", 0)
                parts.append(
                    f"@{username} (fol={followers}, likes={likes}): {text}"
                )
        else:
            parts.append("\n--- NO TWEETS AVAILABLE ---")
            parts.append("Analyze based on on-chain data and scores only.")

        return "\n".join(parts) + extra_context

    # ── Output Parsing ────────────────────────────────────────────────────────

    def _parse_ai_output(self, raw_output: str) -> dict:
        """Parse the LLM JSON output. Returns default if parsing fails."""
        if not raw_output:
            return self._default_result()

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
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("Failed to parse AI JSON output")

        return self._default_result()

    def _default_result(self) -> dict:
        return {
            "ai_trust_score": 50,
            "ai_sentiment_score": 50,
            "verdict": "AI analysis unavailable — using algorithmic scoring only.",
            "red_flags": [],
            "green_flags": [],
            "bot_likelihood": "UNKNOWN",
            "recommendation": "DYOR",
            "scoring_rationale": "No AI evaluation available, defaulting to neutral.",
        }

    # ── Fallback Analysis ─────────────────────────────────────────────────────

    def _fallback_analysis(self, scoring_result: dict) -> dict:
        """
        Rule-based fallback when OpenGradient is unavailable or fails.
        Uses pre-computed scores to generate a basic analysis.
        """
        social = scoring_result.get("social_score", 50)
        bot = scoring_result.get("bot_score", 50)
        holder = scoring_result.get("holder_score", 50)
        avg = (social + bot + holder) // 3

        # Generate flags from scores
        red_flags = []
        green_flags = []

        if bot < 40:
            red_flags.append("Potential bot activity detected (algorithmic)")
        if holder < 40:
            red_flags.append("Holder concentration concerns (algorithmic)")
        if social < 30:
            red_flags.append("Weak social presence")

        if social > 70:
            green_flags.append("Strong organic social activity")
        if bot > 80:
            green_flags.append("Low bot activity detected")
        if holder > 70:
            green_flags.append("Healthy holder distribution")

        # Generate recommendation
        if avg >= 70:
            recommendation = "LOOKS_OK"
            bot_likelihood = "LOW"
        elif avg >= 50:
            recommendation = "DYOR"
            bot_likelihood = "MEDIUM"
        elif avg >= 30:
            recommendation = "RISKY"
            bot_likelihood = "HIGH"
        else:
            recommendation = "AVOID"
            bot_likelihood = "EXTREME"

        return {
            "ai_trust_score": avg,
            "ai_sentiment_score": avg,
            "verdict": f"Algorithmic analysis only (AI verification unavailable). Score: {avg}/100. DYOR.",
            "red_flags": red_flags if red_flags else ["No major red flags from algorithm"],
            "green_flags": green_flags if green_flags else [],
            "bot_likelihood": bot_likelihood,
            "recommendation": recommendation,
            "scoring_rationale": "Rule-based fallback — OpenGradient AI was not available.",
        }
