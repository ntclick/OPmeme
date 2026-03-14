# api/chat.py — Chat logic: detect contracts, route to analysis, build LLM conversations

import re
import logging
import json
from typing import Optional

logger = logging.getLogger(__name__)

# ── Contract detection ────────────────────────────────────────────────────────

# Solana: 32-44 alphanumeric chars (lenient for detection, API will normalize/validate)
_SOL_RE = re.compile(r'\b([a-zA-Z0-9]{32,44})\b')
# EVM 0x address: exactly 42 hex chars
_EVM_RE = re.compile(r'\b(0x[0-9a-fA-F]{40})\b')


_DEX_URL_RE = re.compile(r'dexscreener\.com/[a-zA-Z0-9-]+/([a-zA-Z0-9]+)')


def detect_contract(message: str) -> Optional[str]:
    """Extract first contract address or DexScreener URL, handling potential spaces/typos."""
    # 1. Check for DexScreener URL first (ignore spaces in URL if accidental)
    m = _DEX_URL_RE.search(message.replace(" ", ""))
    if m:
        candidate = m.group(1)
        if len(candidate) >= 32:
            return candidate

    # 2. Try standard matching (unmodified message)
    m = _EVM_RE.search(message)
    if m: return m.group(1)
    m = _SOL_RE.search(message)
    if m:
        candidate = m.group(1)
        if len(candidate) >= 32: return candidate

    # 3. Robust pass: remove ALL whitespace and try matching
    # This catches "9kwb4p7k... lvt4gqay43p" typos
    clean_msg = "".join(message.split())
    m = _EVM_RE.search(clean_msg)
    if m: return m.group(1)
    m = _SOL_RE.search(clean_msg)
    if m:
        candidate = m.group(1)
        if len(candidate) >= 32: return candidate

    return None


def detect_chain(address: str) -> str:
    """Detect blockchain from address format."""
    if address.startswith("0x") and len(address) == 42:
        return "evm"
    if len(address) > 30 and not address.startswith("0x"):
        return "solana"
    return "unknown"


# ── Chat system prompt ────────────────────────────────────────────────────────

CHAT_SYSTEM_PROMPT = """\
You are 'CoinCheckGo Assistant' — an expert crypto analyst chatbot on Solana & EVM chains.
You help users analyze meme coins and tokens through natural conversation.

STYLE:
- Respond strictly in English by default. Only use Vietnamese if the user explicitly asks for it.
- Responses must be English (default). Only use Vietnamese if explicitly requested by the user.
- Be extremely concise and focus on the most important information.
- Do not explain excessively; keep bullet points minimal.
- Use simple, professional, and easy-to-understand language.
- Always include a brief risk warning: "Not financial advice. You can lose 100%."

RULES FOR HANDLING ANALYSIS AND ERRORS:
1. If analysis fails (e.g. wallet address, token not found): State the error clearly in 1-2 sentences in English. Example: "This is a wallet address, not a token contract. Please check again."
2. If format is wrong: Briefly mention the correct format in English and ask to check again.
3. If successful: Summarize key points concisely (Score, Red/Green flags, Verdict).

Response limit: Maximum 150 words. The shorter, the better.
"""


def build_analysis_context(analysis_data: dict) -> str:
    """Build a human-readable context string from analysis API response."""
    if not analysis_data:
        return ""

    ticker = analysis_data.get("ticker", "UNKNOWN")
    score = analysis_data.get("overall_score", "?")
    risk = analysis_data.get("risk_level", "?")
    verdict = analysis_data.get("verdict", "")

    market = analysis_data.get("market_data") or {}
    token_meta = market.get("token") or {}

    lines = [
        f"=== ANALYSIS RESULT FOR ${ticker} ===",
        f"Contract: {analysis_data.get('contract_address', 'N/A')}",
        f"Overall Trust Score: {score}/100 (Risk: {risk})",
        f"",
        f"--- Market Data ---",
        f"Price: ${market.get('price', 'N/A')}",
        f"Market Cap: ${market.get('market_cap', 0):,.0f}" if isinstance(market.get('market_cap'), (int, float)) else "Market Cap: N/A",
        f"Liquidity: ${market.get('liquidity_usd', 0):,.0f}" if isinstance(market.get('liquidity_usd'), (int, float)) else "Liquidity: N/A",
        f"Holders: {market.get('holders', 'N/A'):,}" if isinstance(market.get('holders'), (int, float)) else f"Holders: {market.get('holders', 'N/A')}",
        f"Volume 24h: ${market.get('volume_24h', 0):,.0f}" if isinstance(market.get('volume_24h'), (int, float)) else "Volume 24h: N/A",
        f"Price Change 1h: {market.get('price_change_1h', 0):.1f}%" if isinstance(market.get('price_change_1h'), (int, float)) else "",
        f"Price Change 24h: {market.get('price_change_24h', 0):.1f}%" if isinstance(market.get('price_change_24h'), (int, float)) else "",
        f"Token Age: {market.get('token_age_hours', 'N/A'):.1f} hours" if isinstance(market.get('token_age_hours'), (int, float)) else "",
        f"",
        f"--- Score Breakdown ---",
    ]

    breakdown = analysis_data.get("breakdown") or {}
    tech = breakdown.get("technical") or {}
    onchain = breakdown.get("onchain") or {}

    if tech.get("components"):
        tc = tech["components"]
        lines.append(f"Technical (65%): {tech.get('total', '?')}/100")
        for k, v in tc.items():
            if isinstance(v, dict):
                lines.append(f"  - {k}: {v.get('score', '?')}/100")

    if onchain.get("components"):
        oc = onchain["components"]
        lines.append(f"On-Chain (35%): {onchain.get('total', '?')}/100")
        for k, v in oc.items():
            if isinstance(v, dict):
                lines.append(f"  - {k}: {v.get('score', '?')}/100")

    red = analysis_data.get("red_flags") or []
    green = analysis_data.get("green_flags") or []

    if red:
        lines.append(f"")
        lines.append(f"--- Red Flags ---")
        for f in red:
            lines.append(f"  🔴 {f}")

    if green:
        lines.append(f"")
        lines.append(f"--- Green Flags ---")
        for f in green:
            lines.append(f"  🟢 {f}")

    if verdict:
        lines.append(f"")
        lines.append(f"--- AI Verdict ---")
        lines.append(verdict)

    return "\n".join(lines)


def build_messages(
    user_message: str,
    history: list[dict],
    analysis_context: Optional[str] = None,
) -> list[dict]:
    """Build the message array for the LLM call."""
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]

    # If we have analysis context, inject it as the first assistant context
    if analysis_context:
        messages.append({
            "role": "user",
            "content": f"Here is the analysis data I just ran. Please summarize it conversationally for the user:\n\n{analysis_context}"
        })

    # Add conversation history
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    # Add current user message (if not already a repeat of the last history entry)
    if user_message:
        messages.append({"role": "user", "content": user_message})

    return messages
