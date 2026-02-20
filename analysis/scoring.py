# analysis/scoring.py — Social score, holder score, and overall scoring algorithm

import logging

logger = logging.getLogger(__name__)


# ─── Social Score ──────────────────────────────────────────────────────────────


def calculate_social_score(tweets: list[dict]) -> dict:
    """
    Evaluate Twitter community quality.  Score 0-100.

    Components (weights):
    1. Unique Authors Ratio  (30%) — diverse authors = organic community
    2. Follower Quality      (25%) — median followers + influencer count
    3. Engagement Quality    (25%) — avg likes+replies, engagement depth
    4. Content Diversity     (20%) — n-gram Jaccard duplicate detection

    Returns dict with social_score, sub_scores, and details.
    """
    if not tweets:
        return {"social_score": 0, "details": "No tweets found"}

    # --- 1. Unique Authors Ratio ---
    authors = [t["author_username"] for t in tweets]
    unique_authors = set(authors)
    unique_ratio = len(unique_authors) / len(tweets) if tweets else 0

    # ratio 0.0 → 0, ratio 0.5 → 60, ratio 0.8+ → 100
    author_score = min(100, int(unique_ratio * 125))

    # --- 2. Follower Quality ---
    follower_counts = sorted([t["author_followers"] for t in tweets])
    median_followers = (
        follower_counts[len(follower_counts) // 2] if follower_counts else 0
    )
    influencer_count = sum(1 for f in follower_counts if f > 1000)

    if median_followers >= 2000:
        follower_score = 100
    elif median_followers >= 500:
        follower_score = 60 + int((median_followers - 500) / 1500 * 40)
    elif median_followers >= 100:
        follower_score = 30 + int((median_followers - 100) / 400 * 30)
    else:
        follower_score = int(median_followers / 100 * 30)

    # Bonus for influencers
    influencer_bonus = min(20, influencer_count * 3)
    follower_score = min(100, follower_score + influencer_bonus)

    # --- 3. Engagement Quality ---
    engagements = [t["like_count"] + t["reply_count"] for t in tweets]
    avg_engagement = sum(engagements) / len(engagements) if engagements else 0

    if avg_engagement >= 100:
        engagement_score = 100
    elif avg_engagement >= 20:
        engagement_score = 70 + int((avg_engagement - 20) / 80 * 30)
    elif avg_engagement >= 5:
        engagement_score = 40 + int((avg_engagement - 5) / 15 * 30)
    else:
        engagement_score = max(10, int(avg_engagement / 5 * 40))

    # --- 4. Content Diversity ---
    diversity = _calculate_content_diversity(tweets)
    diversity_score = int(diversity * 100)

    # --- Weighted total ---
    social_score = int(
        author_score * 0.30
        + follower_score * 0.25
        + engagement_score * 0.25
        + diversity_score * 0.20
    )

    return {
        "social_score": max(0, min(100, social_score)),
        "unique_author_ratio": round(unique_ratio, 2),
        "median_followers": median_followers,
        "influencer_count": influencer_count,
        "avg_engagement": round(avg_engagement, 1),
        "content_diversity": round(diversity, 2),
        "sub_scores": {
            "author_score": author_score,
            "follower_score": follower_score,
            "engagement_score": engagement_score,
            "diversity_score": diversity_score,
        },
    }


def _calculate_content_diversity(tweets: list[dict]) -> float:
    """
    Measure content diversity using 3-gram Jaccard similarity.

    Algorithm:
    1. Each tweet → set of 3-grams (3 consecutive words)
    2. Compare each pair with Jaccard similarity
    3. If similarity > 0.6 → counted as "duplicate"
    4. Return: 1 - (duplicate_pairs / total_pairs)

    Why 3-grams instead of heavy NLP?
    → MVP needs speed. 3-grams are sufficient to catch copy-paste bots.
    → Scale up: use sentence-transformers cosine similarity.
    """
    if len(tweets) < 2:
        return 1.0

    def get_ngrams(text: str, n: int = 3) -> set:
        words = text.lower().split()
        if len(words) < n:
            return {text.lower()}
        return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}

    texts_ngrams = [get_ngrams(t["full_text"]) for t in tweets]

    duplicate_count = 0
    total_pairs = 0

    # Limit pairwise comparison to O(n²) manageable size
    sample_size = min(len(texts_ngrams), 50)
    sample = texts_ngrams[:sample_size]

    for i in range(len(sample)):
        for j in range(i + 1, len(sample)):
            total_pairs += 1
            intersection = sample[i] & sample[j]
            union = sample[i] | sample[j]
            if union:
                similarity = len(intersection) / len(union)
                if similarity > 0.6:
                    duplicate_count += 1

    if total_pairs == 0:
        return 1.0

    return 1.0 - (duplicate_count / total_pairs)


# ─── Holder Score ──────────────────────────────────────────────────────────────


def calculate_holder_score(holder_data: dict, token_info: dict) -> dict:
    """
    Evaluate token based on on-chain data.  Score 0-100.

    Components (weights):
    1. Top Holder Concentration (40%) — top10 % of supply
    2. Mint Authority           (30%) — renounced = safe
    3. Liquidity Status         (30%) — LP burned/locked

    Returns dict with holder_score, red_flags, sub_scores.
    """
    if not holder_data:
        return {"holder_score": 50, "details": "No on-chain data available"}

    # --- 1. Concentration ---
    top10_pct = holder_data.get("top10_pct", 50)

    if top10_pct > 80:
        concentration_score = max(0, int(10 - (top10_pct - 80) / 2))
    elif top10_pct > 50:
        concentration_score = int(20 + (80 - top10_pct) / 30 * 20)
    elif top10_pct > 30:
        concentration_score = int(40 + (50 - top10_pct) / 20 * 30)
    else:
        concentration_score = int(70 + (30 - top10_pct) / 30 * 30)

    # --- 2. Mint Authority ---
    if token_info and token_info.get("is_mint_renounced"):
        mint_score = 100
    elif token_info and token_info.get("mint_authority"):
        mint_score = 0  # DANGER
    else:
        mint_score = 50  # Unknown

    # --- 3. LP Status ---
    lp_burned = holder_data.get("lp_burned_pct")
    lp_locked = holder_data.get("liquidity_locked")

    if lp_burned and lp_burned > 90:
        lp_score = 100
    elif lp_locked:
        lp_score = 70
    elif lp_burned and lp_burned > 50:
        lp_score = 50
    else:
        lp_score = 10  # No lock, no burn = danger

    # --- Weighted total ---
    holder_score = int(
        concentration_score * 0.40
        + mint_score * 0.30
        + lp_score * 0.30
    )

    red_flags = []
    if top10_pct > 60:
        red_flags.append(f"Top 10 holders control {top10_pct}% supply")
    if token_info and not token_info.get("is_mint_renounced"):
        red_flags.append(
            "⚠️ Mint authority NOT renounced — dev can print more tokens"
        )
    if not lp_locked and (not lp_burned or lp_burned < 50):
        red_flags.append("⚠️ LP not locked/burned — rug pull possible")

    return {
        "holder_score": max(0, min(100, holder_score)),
        "top10_pct": top10_pct,
        "mint_renounced": token_info.get("is_mint_renounced") if token_info else None,
        "lp_burned_pct": lp_burned,
        "red_flags": red_flags,
        "sub_scores": {
            "concentration_score": concentration_score,
            "mint_score": mint_score,
            "lp_score": lp_score,
        },
    }


# ─── Overall Score ─────────────────────────────────────────────────────────────


def calculate_overall_score(
    social_result: dict,
    bot_result: dict,
    sentiment_score: int,
    holder_result: dict,
    ai_trust_score: int | None = None,
    token_info: dict | None = None,
) -> dict:
    """
    Combine all component scores into a final 0-100 score.
    
    ... (docstring simplified for brevity) ...
    """
    social_s = social_result.get("social_score", 50)
    bot_s = bot_result.get("bot_score", 50)
    sentiment_s = sentiment_score
    holder_s = holder_result.get("holder_score", 50)

    # Weighted average — AI-first when available
    if ai_trust_score is not None:
        raw_score = int(
            ai_trust_score * 0.40
            + social_s * 0.20
            + bot_s * 0.15
            + holder_s * 0.25
        )
        weights = {
            "ai_trust": 0.40,
            "social": 0.20,
            "bot": 0.15,
            "holder": 0.25,
        }
    else:
        # Fallback: no AI → use old weights with sentiment proxy
        raw_score = int(
            social_s * 0.35
            + bot_s * 0.25
            + sentiment_s * 0.15
            + holder_s * 0.25
        )
        weights = {
            "social": 0.35,
            "bot": 0.25,
            "sentiment": 0.15,
            "holder": 0.25,
        }

    # --- Override Rules ---
    override_reasons = []
    max_cap = 100

    # Rule 1: Mint authority
    if holder_result.get("mint_renounced") is False:
        max_cap = min(max_cap, 40)
        override_reasons.append("Mint authority active → capped at 40")

    # Rule 2: Bot army
    if bot_s < 20:
        max_cap = min(max_cap, 50)
        override_reasons.append("Heavy bot activity → capped at 50")

    # Rule 3: Whale concentration
    top10 = holder_result.get("top10_pct", 0)
    if top10 and top10 > 80:
        max_cap = min(max_cap, 30)
        override_reasons.append(f"Top 10 hold {top10}% → capped at 30")

    # Rule 4: Small Cap / New Token Check (User Request)
    # Check Market Cap < $500k OR Age < 48h
    # IF TRUE AND No Reputable KOL (influencer_count == 0) -> Penalize
    if token_info:
        mcap = token_info.get("marketCap", 0) or 0
        created_at_ts = token_info.get("pairCreatedAt", 0) or 0
        
        # Age check
        import time
        now_ts = int(time.time() * 1000)
        age_hours = (now_ts - created_at_ts) / (1000 * 3600) if created_at_ts else 0
        
        is_small_cap = mcap < 500000
        is_new_token = age_hours < 48
        
        has_kol = social_result.get("influencer_count", 0) > 0
        
        if (is_small_cap or is_new_token) and not has_kol:
            max_cap = min(max_cap, 40)
            reason = []
            if is_small_cap: reason.append(f"Small Cap (${mcap:,.0f})") 
            if is_new_token: reason.append(f"New Token ({age_hours:.1f}h)")
            override_reasons.append(f"{' & '.join(reason)} + No KOL → capped at 40")

    # Apply cap
    final_score = min(raw_score, max_cap)
    final_score = max(0, min(100, final_score))

    # Risk level
    if final_score <= 20:
        risk = "EXTREME"
    elif final_score <= 40:
        risk = "HIGH"
    elif final_score <= 60:
        risk = "MEDIUM"
    elif final_score <= 80:
        risk = "LOW"
    else:
        risk = "MOON"

    components = {
        "social_score": social_s,
        "bot_score": bot_s,
        "sentiment_score": sentiment_s,
        "holder_score": holder_s,
    }
    if ai_trust_score is not None:
        components["ai_trust_score"] = ai_trust_score

    return {
        "overall_score": final_score,
        "risk_level": risk,
        "raw_score_before_cap": raw_score,
        "max_cap_applied": max_cap if max_cap < 100 else None,
        "override_reasons": override_reasons,
        "ai_driven": ai_trust_score is not None,
        "components": components,
        "weights": weights,
    }
