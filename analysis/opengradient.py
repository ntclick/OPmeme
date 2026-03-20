import socket
import ssl
import httpx
import logging
import json
import os
from typing import Any, Optional
import opengradient as og

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
You are 'CoinCheckGo Sentinel' — a ruthless, data-driven meme coin auditor on Solana.
Your output is immutable proof verified by TEE. Output ONLY valid JSON.
"""

class OpenGradientAnalyzer:
    """ OpenGradient Analyzer (v2 Proper - Ultimate Resilience Edition) """

    DEFAULT_MODEL = "openai/gpt-4.1-2025-04-14"

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
            
            # Global patches handle everything now
            self._llm_instance = og.LLM(
                private_key=private_key,
                llm_server_url="https://llm.opengradient.ai"
            )
            
            # --- MANDATORY: ENSURE PERMIT2 APPROVAL ---
            # As requested by user: enhance logging and fail-hard on approval issues.
            print("🛡️ Ensuring Permit2 OPG approval (5.0 OPG)...")
            try:
                # Use a larger amount (5.0 OPG) to ensure longevity.
                approval = self._llm_instance.ensure_opg_approval(opg_amount=5.0)
                # approval is typically an OPGApprovalResponse with allowance_before, allowance_after, tx_hash
                logger.info(f"✅ Permit2 status: before={getattr(approval, 'allowance_before', 'N/A')}, after={getattr(approval, 'allowance_after', 'N/A')}, tx={getattr(approval, 'tx_hash', 'None')}")
                if getattr(approval, 'tx_hash', None):
                    print(f"🚀 Approval transaction sent: {approval.tx_hash}")
                    print("⏳ Waiting 60s for server-side indexing...")
                    import time
                    time.sleep(60)
                else:
                    print("✅ Permit2 allowance already sufficient.")
            except Exception as e:
                logger.error(f"❌ CRITICAL: Permit2 approval failed: {e}")
                self._init_error = f"Permit2 approval failed: {e}"
                # Return early without setting _initialized = True
                return

            # Model mapping (Updated to match SDK TEE_LLM enum exactly)
            self.model_map = {
                "google/gemini-2.5-flash": og.TEE_LLM.GEMINI_2_5_FLASH,
                "google/gemini-2.5-pro": og.TEE_LLM.GEMINI_2_5_PRO,
                "anthropic/claude-4.0-sonnet": og.TEE_LLM.CLAUDE_SONNET_4_5,
                "anthropic/claude-3.7-sonnet": og.TEE_LLM.CLAUDE_SONNET_4_6,
                "openai/gpt-4o": og.TEE_LLM.GPT_5, # SDK docs imply GPT_5 is the flagship
                "openai/gpt-4.1-2025-04-14": og.TEE_LLM.GPT_4_1_2025_04_14,
            }

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
                x402_settlement_mode=og.x402SettlementMode.BATCH_HASHED
            )
            return {"content": str(result.chat_output), "error": None}
        except Exception as e:
            return {"content": None, "error": str(e)}

    async def analyze_coin(self, ticker: str, tweets_summary: list, on_chain_data: dict, scoring_result: dict, llm_model: Optional[str] = None) -> dict:
        if not self._initialized: return {"error": self._init_error}
        model_str = llm_model or self.DEFAULT_MODEL
        input_text = f"Analyze ${ticker}\nScores: {json.dumps(scoring_result)}\nData: {json.dumps(on_chain_data)}"
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": input_text}]
        try:
            current_model = self.model_map.get(model_str, og.TEE_LLM.GPT_4_1_2025_04_14)
            completion = await self._llm_instance.chat(
                model=current_model,
                messages=messages,
                max_tokens=2000,
                temperature=0.1,
                x402_settlement_mode=og.x402SettlementMode.INDIVIDUAL_FULL
            )
            raw = str(completion.chat_output)
            try:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                ai_result = json.loads(raw[start:end])
            except: ai_result = {"verdict": raw}
            return {"ai_result": ai_result, "tx_hash": getattr(completion, "transaction_hash", None), "used_opengradient": True}
        except Exception as e:
            logger.error(f"Inference failed: {e}")
            return {"error": str(e), "used_opengradient": False}

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
