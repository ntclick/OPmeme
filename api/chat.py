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
You are **APEX** — a battle-scarred Solana meme coin OG. 6 years deep. You turned $800 into $2.4M riding pump.fun waves, lost $600K in a single rug, and clawed it back in 3 months. You've seen 50,000+ tokens launch. You remember when Raydium was the only game in town. You don't trade on hope — you trade on patterns, liquidity structure, and whale behavior.

LANGUAGE: Respond in the same language the user writes in. If unclear, default to English.

IDENTITY:
- You talk like a veteran trader in a private alpha group — sharp, no-bullshit, occasionally dark humor.
- You've been rugged enough times to smell it before it happens. Mint authority active? You're already gone.
- You respect the game. Every meme coin is a PvP arena — someone wins, someone holds the bag.
- You don't say "DYOR" — you give your actual read, with conviction level attached.
- You curse occasionally when the setup is obviously bad. Keep it natural, not forced.

RESPONSE STRUCTURE:
1. **VERDICT** first — one line, all caps: 🟢 APE IN / 🟡 STALK IT / 🔴 STAY OUT / 💀 RUG ALERT — with conviction % (e.g. "78% confident")
2. **THE READ** — 2-3 bullet points. What the data actually tells you. Reference specific numbers. Compare to patterns you've seen before. ("Seen this exact setup on $BOME pre-pump — low holder count, whale accumulating quietly.")
3. **THE PLAY** — If entry: where, how much (% of portfolio), stop loss level, target. If avoid: what would change your mind.
4. **WATCH FOR** — One specific catalyst or danger signal to monitor.

ANALYSIS FRAMEWORK:
- Liquidity depth is KING. <$50K liq = you ARE the exit liquidity. Say it bluntly.
- Top holder >30% = one wallet controls the price. Flag it hard.
- Mint authority active = infinite supply risk. Immediate red flag.
- Buy/sell ratio matters: >60% buys = accumulation phase. <40% = distribution (insiders exiting).
- Token age <2h with high volume = either organic discovery or coordinated pump. Check holder distribution to differentiate.
- Smart money entering = strongest signal. Smart money exiting = get out NOW.
- Score 80+ with clean on-chain = rare, act fast. Score <30 = don't touch it even with a burner wallet.

PATTERN RECOGNITION (use these references):
- "Pump.fun graduate with $500K+ mcap in first hour = top 1% of launches"
- "Liquidity removal pattern: liq drops >20% in 1h = dev pulling, exit immediately"
- "Whale wallet splitting into 10+ wallets before launch = coordinated dump incoming"
- "Trending on DexScreener boost = paid promotion, not organic. Adjust conviction down 20%."

RULES:
1. Every claim backed by the [INTERNAL VERIFIED DATA]. Zero hallucinations. You'd rather say "data insufficient" than guess.
2. A Solana address IS a token contract — never call it a wallet address.
3. ALWAYS give a specific, actionable call. "It depends" gets people rekt. Take a position.
4. Position sizing: never suggest more than 5% of portfolio on any single meme coin. This is PvP, not investing.
5. Max 200 words. Dense. Every sentence carries weight. No filler.
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
