# analysis/bot_detector.py — Bot/spam detection for tweet sets

import re
from collections import Counter
import logging

logger = logging.getLogger(__name__)


def _normalize_text(text: str) -> str:
    """Normalize text for duplicate detection."""
    text = re.sub(r'https?://\S+', '', text)  # Remove URLs
    text = re.sub(r'@\w+', '', text)          # Remove mentions
    text = re.sub(r'#\w+', '', text)          # Remove hashtags
    text = re.sub(r'\s+', ' ', text)          # Collapse whitespace
    return text.strip().lower()

def calculate_bot_score(tweets: list[dict]) -> dict:
    """
    Detect bot/spam activity.  Score 0-100  (high = FEW bots = GOOD).

    Components (weights):
    1. Duplicate Content    (40%) — exact-text duplicates
    2. Posting Pattern      (20%) — burst posting in same minute
    3. Account Quality      (20%) — low-follower / bot-named accounts
    4. Engagement Anomaly   (20%) — high RT but 0 likes/replies

    Returns dict with bot_score, duplicate_pct, suspicious_accounts_pct,
    and human-readable bot_patterns_found list.
    """
    if not tweets:
        return {"bot_score": 50, "bot_patterns_found": ["No data"]}

    patterns_found: list[str] = []

    # --- 1. Duplicate Content (40%) ---
    texts = [_normalize_text(t["full_text"]) for t in tweets]
    text_counts: dict[str, int] = {}
    for text in texts:
        if text: # Ignore fully empty texts after norm
            text_counts[text] = text_counts.get(text, 0) + 1

    duplicates = sum(count - 1 for count in text_counts.values() if count > 1)
    duplicate_pct = duplicates / len(tweets) if tweets else 0

    if duplicate_pct > 0.5:
        dup_score = 0
        patterns_found.append(
            f"CRITICAL: {int(duplicate_pct * 100)}% duplicate tweets — BOT ARMY"
        )
    elif duplicate_pct > 0.3:
        dup_score = 30
        patterns_found.append(
            f"WARNING: {int(duplicate_pct * 100)}% duplicate tweets"
        )
    elif duplicate_pct > 0.1:
        dup_score = 60
        patterns_found.append(f"Minor duplicates: {int(duplicate_pct * 100)}%")
    else:
        dup_score = 100

    # --- 2. Posting Pattern (20%) ---
    timestamps = [
        t.get("tweet_created_at")
        for t in tweets
        if t.get("tweet_created_at")
    ]
    time_score = 80  # Default OK if no timestamp data

    if len(timestamps) > 10:
        # Truncate to minute precision
        minutes = [
            ts[:16] if isinstance(ts, str) else "" for ts in timestamps
        ]
        minute_counts = Counter(minutes)
        max_per_minute = max(minute_counts.values()) if minute_counts else 0

        if max_per_minute > len(tweets) * 0.3:
            time_score = 20
            patterns_found.append(
                f"Burst posting: {max_per_minute} tweets in same minute"
            )
        elif max_per_minute > len(tweets) * 0.15:
            time_score = 50
            patterns_found.append("Moderate posting bursts detected")

    # --- 3. Account Quality (20%) ---
    low_follower_accounts = sum(
        1 for t in tweets if t["author_followers"] < 10
    )
    low_follower_pct = low_follower_accounts / len(tweets)

    bot_username_pattern = re.compile(r"^[a-z]+\d{6,}$", re.IGNORECASE)
    bot_usernames = sum(
        1 for t in tweets if bot_username_pattern.match(t["author_username"])
    )
    bot_username_pct = bot_usernames / len(tweets)

    suspicious_pct = (low_follower_pct + bot_username_pct) / 2

    if suspicious_pct > 0.5:
        account_score = 10
        patterns_found.append(
            f"High % suspicious accounts ({int(suspicious_pct * 100)}%)"
        )
    elif suspicious_pct > 0.3:
        account_score = 40
        patterns_found.append(
            f"Some suspicious accounts ({int(suspicious_pct * 100)}%)"
        )
    else:
        account_score = 80 + int((1 - suspicious_pct) * 20)

    # --- 4. Engagement Anomaly (20%) ---
    zero_engagement = sum(
        1
        for t in tweets
        if t["like_count"] == 0
        and t["reply_count"] == 0
        and t["retweet_count"] > 5
    )
    zero_eng_pct = zero_engagement / len(tweets)

    if zero_eng_pct > 0.3:
        engagement_anomaly_score = 20
        patterns_found.append(
            "Engagement anomaly: high RT but 0 likes/replies"
        )
    else:
        engagement_anomaly_score = 80 + int((1 - zero_eng_pct) * 20)

    # --- Weighted total ---
    bot_score = int(
        dup_score * 0.40
        + time_score * 0.20
        + account_score * 0.20
        + engagement_anomaly_score * 0.20
    )

    return {
        "bot_score": max(0, min(100, bot_score)),
        "duplicate_pct": round(duplicate_pct, 2),
        "suspicious_accounts_pct": round(suspicious_pct, 2),
        "bot_patterns_found": (
            patterns_found if patterns_found else ["No bot patterns detected"]
        ),
    }
