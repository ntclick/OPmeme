# analysis/scoring.py — Social score, holder score, and overall scoring algorithm

import logging
import time

logger = logging.getLogger(__name__)


# ─── Import new analyzers for v2 ───────────────────────────────────────────────
from analysis.technical_analyzer import TechnicalAnalyzer
from analysis.liquidity_analyzer import LiquidityAnalyzer


# ─── Social Score ──────────────────────────────────────────────────────────────
# analysis/scoring.py — Technical, On-Chain, and Social scoring algorithm (v3.0)

import logging
import time

logger = logging.getLogger(__name__)

from analysis.technical_analyzer import TechnicalAnalyzer
from analysis.liquidity_analyzer import LiquidityAnalyzer

def _calculate_content_diversity(tweets: list[dict]) -> float:
    if len(tweets) < 2: return 1.0
    def get_ngrams(text: str, n: int = 3) -> set:
        words = text.lower().split()
        if len(words) < n: return {text.lower()}
        return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}
    texts_ngrams = [get_ngrams(t["full_text"]) for t in tweets]
    duplicate_count, total_pairs = 0, 0
    sample = texts_ngrams[:min(len(texts_ngrams), 50)]
    for i in range(len(sample)):
        for j in range(i + 1, len(sample)):
            total_pairs += 1
            if len(sample[i] | sample[j]):
                if len(sample[i] & sample[j]) / len(sample[i] | sample[j]) > 0.6:
                    duplicate_count += 1
    return 1.0 if total_pairs == 0 else 1.0 - (duplicate_count / total_pairs)


def calculate_social_score(tweets: list[dict]) -> dict:
    if not tweets: return {"social_score": 0, "details": "No tweets found"}
    # 1. Unique Authors
    authors = [t["author_username"] for t in tweets]
    unique_ratio = len(set(authors)) / len(tweets) if tweets else 0
    author_score = min(100, int(unique_ratio * 125))

    # 2. Followers
    fc = sorted([t["author_followers"] for t in tweets])
    mf = fc[len(fc) // 2] if fc else 0
    ic = sum(1 for f in fc if f > 1000)
    if mf >= 2000: fs = 100
    elif mf >= 500: fs = 60 + int((mf - 500) / 1500 * 40)
    elif mf >= 100: fs = 30 + int((mf - 100) / 400 * 30)
    else: fs = int(mf / 100 * 30)
    fs = min(100, fs + min(20, ic * 3))

    # 3. Engagements
    engs = [t["like_count"] + t["reply_count"] for t in tweets]
    ae = sum(engs) / len(engs) if engs else 0
    if ae >= 100: es = 100
    elif ae >= 20: es = 70 + int((ae - 20) / 80 * 30)
    elif ae >= 5: es = 40 + int((ae - 5) / 15 * 30)
    else: es = max(10, int(ae / 5 * 40))

    ds = int(_calculate_content_diversity(tweets) * 100)
    
    # Simple social proxy mapping (Community 10% / Bot 5%)
    sc = int(author_score * 0.4 + es * 0.3 + fs * 0.3)
    bs = int(ds * 0.5 + 50) # Bot score proxy based on duplicate detection

    return {
        "social_score": max(0, min(100, int((sc * 2 + bs) / 3))),
        "community_score": sc,
        "bot_score": bs,
        "influencer_count": ic
    }


def calculate_holder_score(holder_data: dict, security_data: dict) -> dict:
    """5. Holder Score (12%)"""
    total_holders = holder_data.get("total_holders", 0) if holder_data else 0
    
    if total_holders > 100_000: score = 100
    elif total_holders >= 50_000: score = 90
    elif total_holders >= 20_000: score = 80
    elif total_holders >= 10_000: score = 70
    elif total_holders >= 5_000: score = 60
    elif total_holders >= 2_000: score = 50
    elif total_holders >= 1_000: score = 35
    elif total_holders >= 500: score = 25
    elif total_holders >= 100: score = 15
    else: score = 5
    
    return {
        "holder_score": score,
        "total_holders": total_holders,
        "mint_renounced": security_data.get("mintAuthority") is None if security_data else False
    }


def calculate_security_score(security_data: dict, token_age_hours: float) -> dict:
    """6. Security Score (12%)"""
    if not security_data: 
        return {"security_score": 50, "details": ["No security data available"]}
    
    # Mint Authority (40%)
    mint_score = 100 if security_data.get("mintAuthority") is None else 0
    
    # Freeze Authority (25%)
    freeze_score = 100 if security_data.get("freezeAuthority") is None else 20
    
    # Token Age (35%)
    if token_age_hours >= 2160: age_score = 100 # >90 days
    elif token_age_hours >= 720: age_score = 85 # >30 days
    elif token_age_hours >= 336: age_score = 70 # >14 days
    elif token_age_hours >= 168: age_score = 55 # >7 days
    elif token_age_hours >= 72: age_score = 40  # >3 days
    elif token_age_hours >= 24: age_score = 25  # >1 day
    else: age_score = 10
    
    final_score = int(mint_score * 0.40 + freeze_score * 0.25 + age_score * 0.35)
    
    return {
        "security_score": final_score,
        "mint_score": mint_score,
        "freeze_score": freeze_score,
        "age_score": age_score
    }


def calculate_whale_risk_score(top10_percent: float) -> int:
    """7. Whale Risk Score (6%)"""
    if top10_percent < 20: return 100
    if top10_percent <= 30: return 85
    if top10_percent <= 40: return 70
    if top10_percent <= 50: return 55
    if top10_percent <= 60: return 40
    if top10_percent <= 70: return 25
    if top10_percent <= 80: return 15
    return 5


async def calculate_overall_score(
    birdeye_data: dict,
    dex_data: dict,
    security_data: dict,
    social_result: dict,
    token_age_hours: float = 0
) -> dict:
    """
    Assemble the scores according to v3.0 specs (Technical 55%, On-Chain 30%, Social 15%)
    """
    ta = TechnicalAnalyzer()
    la = LiquidityAnalyzer()
    
    # --- TECHNICAL ---
    m_score_dict = ta.calculate_momentum_score({
        "1h": birdeye_data.get("priceChange1hPercent", 0),
        "6h": dex_data.get("priceChange", {}).get("h6", 0),
        "24h": birdeye_data.get("priceChange24hPercent", 0),
    })
    v_score_dict = ta.calculate_volume_score(birdeye_data)
    tp_score_dict = ta.calculate_trade_pressure_score(birdeye_data)
    l_score_dict = la.calculate_liquidity_score(birdeye_data, {}) # Assuming we don't have holder_data for LP yet
    
    momentum_score = m_score_dict["momentum_score"]
    volume_score = v_score_dict["volume_score"]
    trade_pressure_score = tp_score_dict["trade_pressure_score"]
    liquidity_score = l_score_dict["liquidity_score"]
    
    technical_total = (
        momentum_score * 0.20 +
        volume_score * 0.15 +
        trade_pressure_score * 0.10 +
        liquidity_score * 0.10
    )
    
    # --- ON-CHAIN ---
    holder_score = calculate_holder_score(birdeye_data.get("holder", 0))
    security_score = calculate_security_score(security_data, token_age_hours)
    whale_score = calculate_whale_risk_score(security_data.get("top10HolderPercent", 50))
    
    onchain_total = (
        holder_score * 0.12 +
        security_score * 0.12 +
        whale_score * 0.06
    )
    
    # --- SOCIAL ---
    community_score = social_result.get("community_score", 50)
    bot_score = social_result.get("bot_score", 50)
    social_total = (
        community_score * 0.10 +
        bot_score * 0.05
    )
    
    # --- FINAL CALCULATIONS ---
    raw_score = technical_total + onchain_total + social_total
    
    # Override Rules
    max_cap = 100
    override_reasons = []
    
    if security_data and security_data.get("mintAuthority") is not None:
        max_cap = min(max_cap, 35)
        override_reasons.append("🔴 Mint authority ACTIVE → max 35")
        
    if birdeye_data.get("liquidity", 0) < 10000:
        max_cap = min(max_cap, 30)
        override_reasons.append("🔴 Liquidity < $10K → max 30")
        
    if security_data and security_data.get("top10HolderPercent", 0) > 80:
        max_cap = min(max_cap, 30)
        override_reasons.append("🔴 Top 10 hold > 80% → max 30")
        
    if birdeye_data.get("holder", 0) < 100:
        max_cap = min(max_cap, 35)
        override_reasons.append("🔴 Holders < 100 → max 35")
        
    if security_data and security_data.get("freezeAuthority") is not None:
        max_cap = min(max_cap, 50)
        override_reasons.append("🟡 Freeze authority active → max 50")
        
    final_score = min(raw_score, max_cap)
    final_score = max(0, min(100, int(final_score)))
    
    if final_score >= 80: risk = "MOON"
    elif final_score >= 65: risk = "LOW"
    elif final_score >= 50: risk = "MEDIUM"
    elif final_score >= 35: risk = "HIGH"
    elif final_score >= 20: risk = "EXTREME"
    else: risk = "AVOID"

    # Gather signals for UI
    all_red_flags = [f for f in override_reasons if "🔴" in f or "🟡" in f]
    all_green_flags = []
    
    if security_data and security_data.get("mintAuthority") is None:
        all_green_flags.append("🟢 Mint authority renounced")
    
    if security_data and security_data.get("freezeAuthority") is None:
        all_green_flags.append("🟢 No freeze authority")
    
    return {
        "overall_score": final_score,
        "risk_level": risk,
        "raw_score_before_cap": int(raw_score),
        "max_cap_applied": max_cap if max_cap < 100 else None,
        "override_reasons": override_reasons,
        
        "breakdown": {
            "technical": {
                "total": round(technical_total, 1),
                "weight": "55%",
                "components": {
                    "momentum": {"score": momentum_score, "weight": "20%"},
                    "volume": {"score": volume_score, "weight": "15%"},
                    "trade_pressure": {"score": trade_pressure_score, "weight": "10%"},
                    "liquidity": {"score": liquidity_score, "weight": "10%"},
                }
            },
            "onchain": {
                "total": round(onchain_total, 1),
                "weight": "30%",
                "components": {
                    "holder": {"score": holder_score, "weight": "12%"},
                    "security": {"score": security_score, "weight": "12%"},
                    "whale_risk": {"score": whale_score, "weight": "6%"},
                }
            },
            "social": {
                "total": round(social_total, 1),
                "weight": "15%",
                "components": {
                    "community": {"score": community_score, "weight": "10%"},
                    "bot_detection": {"score": bot_score, "weight": "5%"},
                }
            }
        },
        "red_flags": all_red_flags,
        "green_flags": all_green_flags,
        "technical_signals": {
            "rsi": m_score_dict.get("rsi"),
            "rsi_signal": m_score_dict.get("rsi_signal"),
            "divergence": m_score_dict.get("divergence"),
            "trend": m_score_dict.get("trend"),
            "buy_sell_ratio": tp_score_dict.get("buy_sell_ratio"),
        }
    }
