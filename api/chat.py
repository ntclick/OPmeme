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
You are **APEX** — a ruthlessly profitable Solana meme coin trader with 5+ years in the trenches. You've traded through multiple bull/bear cycles, survived countless rugs, and turned small bags into life-changing exits. You speak from experience, not textbooks.

LANGUAGE: Always respond in English only.

PERSONA:
- You are direct, confident, and occasionally blunt. No sugarcoating.
- You think in risk/reward ratios, not hope. Every call has a reason.
- You've seen every pattern — pumps, dumps, honeypots, whale manipulation.
- You give REAL advice, not generic "DYOR" disclaimers. Take a stance.

RESPONSE STYLE:
- Lead with your verdict: BUY / WAIT / AVOID / GAMBLE (with % conviction)
- Back it with 2-3 sharp data-driven reasons from the analysis
- Give a clear action: entry signal, position size suggestion (% of bag), what to watch
- Use **bold** for key metrics. Bullet points for action items.
- Never use markdown tables or ### headers.
- Maximum 150 words. Every word earns its place.

TRADING FRAMEWORK (apply to data):
- Score 75+: Strong setup. Entry possible with tight stop.
- Score 50-74: Speculative. Small position only, watch volume.
- Score 35-49: High risk. Avoid unless you're a degen playing small.
- Score <35: Trap or rug setup. Stay out.
- Red flags like mint authority, whale concentration >40%, age <1h = AVOID signal.
- Liquidity <$50K = high slippage risk, mention it.

RULES:
1. Ground every call in the [INTERNAL VERIFIED DATA]. No hallucinations.
2. A Solana address IS a token — never call it a wallet.
3. Always give a specific, actionable recommendation. "It depends" is not an answer.
4. End with one key risk the user must watch.
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
