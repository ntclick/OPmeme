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
    """Extract first Solana contract address or DexScreener URL. EVM is disabled."""
    # 1. Check for DexScreener URL first
    m = _DEX_URL_RE.search(message.replace(" ", ""))
    if m:
        candidate = m.group(1)
        if len(candidate) >= 32:
            # Basic length check for Solana-like address
            return candidate

    # 2. Try standard matching for Solana (skip EVM)
    m = _SOL_RE.search(message)
    if m:
        candidate = m.group(1)
        if len(candidate) >= 32: return candidate

    # 3. Robust pass: remove ALL whitespace and try matching Solana
    clean_msg = "".join(message.split())
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
You are 'CoinCheckGo Assistant' — a sharp, expert crypto analyst and experienced trader.
Your mission is to provide professional, no-nonsense insights on tokens across the Solana ecosystem ONLY.

SCOPE & RESTRICTIONS:
- ONLY answer questions related to Solana cryptocurrency, Solana blockchain, and Solana token market analysis.
- WE ONLY SUPPORT SOLANA. If a user asks about EVM (Ethereum, BSC, Base, etc.) or non-crypto topics, politely but firmly decline: "Hệ thống hiện tại chỉ hỗ trợ mạng Solana. Hãy quay lại với các token trên Solana nhé." (or English equivalent: "We currently only support the Solana network. Let's stick to Solana tokens.")

STYLE & TONE:
- Tone: Sharp, authoritative, and professional. Speak like a veteran trader.
- Responses must be 'gãy gọn' (concise) and direct.
- Language: Respond in the user's language (Vietnamese or English).
- Always include a brief risk warning: "Not financial advice. You can lose 100%." (Translation: "Đây không phải là lời khuyên tài chính. Bạn có thể mất hoàn toàn vốn đầu tư.")

RULES FOR HANDLING ANALYSIS:
1. If the system provides analysis data: Use it! Ground your response strictly in that data.
2. If the user provides a Solana address and the system provides analysis data for it, IT IS A VALID TOKEN. DO NOT call it a wallet address.
3. Explicitly mention that you have verified the data on sources like DexScreener/Birdeye.

Response limit: Maximum 150 words. Be sharp.
"""


def build_analysis_context(analysis_data: dict, query_address: Optional[str] = None) -> str:
    """Build a human-readable context string from analysis API response."""
    if not analysis_data:
        return ""

    ticker = analysis_data.get("ticker", "UNKNOWN")
    score = analysis_data.get("overall_score", "?")
    risk = analysis_data.get("risk_level", "?")
    verdict = analysis_data.get("verdict", "")
    contract = analysis_data.get("contract_address", "N/A")

    market = analysis_data.get("market_data") or {}

    lines = [
        f"=== SYSTEM ANALYSIS RESULT ===",
    ]
    if query_address:
        lines.append(f"Query Address: {query_address} (Verified as token ${ticker})")
    
    lines.extend([
        f"Analyzed Token: {contract} (Ticker: ${ticker})",
        f"Overall Trust Score: {score}/100 (Risk: {risk})",
        f"",
        f"--- Market Data (Verified via DexScreener/Birdeye) ---",
        f"Price: ${market.get('price', 'N/A')}",
        f"Market Cap: ${market.get('market_cap', 0):,.0f}" if isinstance(market.get('market_cap'), (int, float)) else "Market Cap: N/A",
        f"Liquidity: ${market.get('liquidity_usd', 0):,.0f}" if isinstance(market.get('liquidity_usd'), (int, float)) else "Liquidity: N/A",
        f"Holders: {market.get('holders', 'N/A'):,}" if isinstance(market.get('holders'), (int, float)) else f"Holders: {market.get('holders', 'N/A')}",
        f"Volume 24h: ${market.get('volume_24h', 0):,.0f}" if isinstance(market.get('volume_24h'), (int, float)) else "Volume 24h: N/A",
        f"Token Age: {market.get('token_age_hours', 'N/A'):.1f} hours" if isinstance(market.get('token_age_hours'), (int, float)) else "",
        f"",
        f"--- Score Breakdown ---",
    ])

    breakdown = analysis_data.get("breakdown") or {}
    tech = breakdown.get("technical") or {}
    onchain = breakdown.get("onchain") or {}

    lines.append(f"Technical (65%): {tech.get('total', '?')}/100")
    lines.append(f"On-Chain (35%): {onchain.get('total', '?')}/100")

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
        lines.append(f"--- Verdict ---")
        lines.append(verdict)

    return "\n".join(lines)


def build_messages(
    user_message: str,
    history: list[dict],
    analysis_context: Optional[str] = None,
) -> list[dict]:
    """Build the message array for the LLM call."""
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]

    # 1. Add conversation history
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    # 2. Inject analysis context as an internal "assistant" grounding message if present
    if analysis_context:
        messages.append({
            "role": "assistant",
            "content": f"[INTERNAL VERIFIED DATA]\n{analysis_context}\n[END DATA]"
        })

    # 3. Add current user message
    if user_message:
        messages.append({"role": "user", "content": user_message})

    return messages
