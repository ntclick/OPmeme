# analysis/sentiment.py — Simple rule-based sentiment analysis (MVP proxy)

import re
import logging

logger = logging.getLogger(__name__)

# Keyword dictionaries for crypto-specific sentiment
POSITIVE_KEYWORDS = {
    "bullish", "moon", "mooning", "pump", "gem", "lfg", "let's go",
    "buy", "buying", "bought", "holding", "hodl", "hold", "accumulate",
    "undervalued", "100x", "1000x", "diamond hands", "chad",
    "degen play", "based", "safu", "legit", "audit", "audited",
    "partnership", "listing", "launch", "airdrop",
    "strong community", "organic", "bullrun", "breakout",
    "love", "amazing", "fire", "🔥", "🚀", "💎", "🐂",
    "good", "great", "best", "solid", "real",
}

NEGATIVE_KEYWORDS = {
    "bearish", "dump", "dumping", "rug", "rugged", "rugpull", "rug pull",
    "scam", "scammer", "fraud", "ponzi", "honeypot", "honey pot",
    "sell", "selling", "sold", "jeet", "jeets", "paper hands",
    "overvalued", "dead", "dying", "rekt", "exit scam",
    "fake", "bot", "bots", "shill", "shilling", "paid promotion",
    "avoid", "warning", "beware", "careful", "sus", "suspicious",
    "no audit", "unaudited", "mint not renounced",
    "whale dump", "dev sold", "dev dump", "insider",
    "hate", "terrible", "worst", "bad", "awful",
    "🔴", "💀", "☠️", "🚨", "⚠️",
}


def calculate_sentiment_score(tweets: list[dict]) -> dict:
    """
    Simple keyword-based sentiment analysis for crypto tweets.
    Score 0–100: 0 = extremely negative, 50 = neutral, 100 = very positive.

    Algorithm:
    1. For each tweet, count positive and negative keyword matches
    2. Weight by engagement (high-engagement tweets matter more)
    3. Compute weighted average sentiment

    This is a proxy — final sentiment comes from OpenGradient AI.
    """
    if not tweets:
        return {"sentiment_score": 50, "details": "No tweets to analyze"}

    total_weighted_sentiment = 0.0
    total_weight = 0.0

    positive_count = 0
    negative_count = 0
    neutral_count = 0

    for tweet in tweets:
        text = tweet["full_text"].lower()

        # Count keyword matches
        pos_matches = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
        neg_matches = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)

        # Determine per-tweet sentiment (0–100)
        if pos_matches + neg_matches == 0:
            tweet_sentiment = 50  # Neutral
            neutral_count += 1
        else:
            # Ratio of positive to total keyword matches
            ratio = pos_matches / (pos_matches + neg_matches)
            tweet_sentiment = int(ratio * 100)
            if tweet_sentiment > 55:
                positive_count += 1
            elif tweet_sentiment < 45:
                negative_count += 1
            else:
                neutral_count += 1

        # Weight by engagement (1 + log-scale of likes+retweets)
        engagement = tweet["like_count"] + tweet["retweet_count"]
        weight = 1.0 + (engagement ** 0.5) * 0.1  # sqrt scaling

        total_weighted_sentiment += tweet_sentiment * weight
        total_weight += weight

    if total_weight == 0:
        return {"sentiment_score": 50, "details": "Could not compute"}

    final_score = int(total_weighted_sentiment / total_weight)
    final_score = max(0, min(100, final_score))

    return {
        "sentiment_score": final_score,
        "positive_tweets": positive_count,
        "negative_tweets": negative_count,
        "neutral_tweets": neutral_count,
        "total_analyzed": len(tweets),
    }
