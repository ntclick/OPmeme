# scrapers/twitter_scraper.py — Twitter Scraper (CamoFox/Nitter + Apify fallback)

from apify_client import ApifyClient
from config import APIFY_API_TOKEN
from typing import Optional
import logging
import asyncio
import concurrent.futures
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)

# Thread pool for running async scraper in a separate thread
# (avoids conflict with FastAPI's running event loop)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)




def _run_nitter_in_thread(query: str, limit: int) -> list[dict]:
    """Run NitterScraper in a separate thread with its own event loop."""
    import sys
    from .nitter_scraper import NitterScraper

    # Playwright requires ProactorEventLoop on Windows for subprocesses
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
        
    asyncio.set_event_loop(loop)
    try:
        scraper = NitterScraper()
        return loop.run_until_complete(scraper.get_tweets(query, limit=limit))
    finally:
        loop.close()


class TwitterScraper:
    """
    Twitter data scraper.
    Priority: Nitter (free, Playwright) → Apify (paid fallback).
    """

    ACTOR_ID = "apidojo/twitter-scraper-lite"

    def __init__(self):
        self.client = ApifyClient(APIFY_API_TOKEN)

    def scrape_by_ticker(
        self,
        ticker: str,
        max_tweets: int = 40,
        language: Optional[str] = None,
    ) -> list[dict]:
        """
        Scrape tweets related to a ticker symbol.

        Strategy:
        1. Try Nitter (free, via Playwright) — runs in a separate thread.
        2. If Nitter fails, fall back to Apify (paid).
        3. Normalize all results to a consistent format.
        """
        queries = [
            f"${ticker}",   # Cashtag: $PEPE
            f"#{ticker}",   # Hashtag: #PEPE
        ]

        # ── STEP 1: Try Nitter (Free) via Playwright ──
        try:
            combined_query = " OR ".join(queries)
            logger.info(f"[Nitter/Playwright] Attempting scrape for: {combined_query}")

            # Run scraper in a separate thread to avoid event loop conflicts
            future = _executor.submit(_run_nitter_in_thread, combined_query, max_tweets)
            nitter_tweets = future.result(timeout=30)  # Reduced to 30s

            if nitter_tweets:
                logger.info(f"[Nitter] Got {len(nitter_tweets)} tweets.")
                # Normalize format → standard format
                normalized = self._normalize_nitter_tweets(nitter_tweets)
                if normalized:
                    return normalized[:max_tweets]

            logger.warning("[Nitter] No usable tweets returned.")

        except concurrent.futures.TimeoutError:
            logger.error("[Nitter] Timed out after 30s.")
        except Exception as e:
            logger.error(f"[Nitter] Failed: {type(e).__name__}: {e}")

        # ── STEP 2: Fallback to Apify (Paid) ──
        # DISABLED: Apify free plan reached ($0.02 credit limit).
        logger.info("[Apify] Fallback disabled (free plan limit). Skipping.")
        all_tweets = []

        # ── STEP 3: Normalize and return ──
        normalized = self._normalize_apify_tweets(all_tweets)
        deduplicated = self._deduplicate(normalized)
        deduplicated.sort(
            key=lambda t: t["like_count"] + t["retweet_count"],
            reverse=True,
        )
        return deduplicated[:max_tweets]

    # ─────────────────────────────────────────────
    # Normalization
    # ─────────────────────────────────────────────

    def _normalize_nitter_tweets(self, raw_items: list[dict]) -> list[dict]:
        """Normalize X.com scraper output to our standard format."""
        normalized = []
        for item in raw_items:
            try:
                author = item.get("author", {}) or {}
                text = item.get("text", "").strip()
                if not text:
                    continue

                tweet_id = item.get("id", "")
                if not tweet_id:
                    # Fallback ID generation
                    unique_str = f"{text}{author.get('name', '')}{item.get('createdAt', '')}"
                    tweet_id = hashlib.md5(unique_str.encode()).hexdigest()

                tweet = {
                    "tweet_id": tweet_id,
                    "author_username": author.get("userName", "unknown"),
                    "author_name": author.get("name", ""),
                    "author_followers": int(author.get("followers", 0) or 0),
                    "author_verified": bool(author.get("verified", False)),
                    "full_text": text,
                    "retweet_count": int(item.get("retweetCount", 0) or 0),
                    "like_count": int(item.get("likeCount", 0) or 0),
                    "reply_count": int(item.get("replyCount", 0) or 0),
                    "quote_count": int(item.get("quoteCount", 0) or 0),
                    "is_retweet": item.get("isRetweet", False),
                    "is_quote": item.get("isQuote", False),
                    "language": item.get("lang", "unknown"),
                    "tweet_created_at": self._parse_nitter_date(item.get("timestamp") or item.get("createdAt")),
                }
                normalized.append(tweet)

            except Exception as e:
                logger.warning(f"Failed to normalize tweet: {e}")
                continue

        return normalized

    def _parse_nitter_date(self, date_str: str) -> str:
        """Parse Nitter date format 'Feb 20, 2026 · 12:18 AM UTC' to ISO string."""
        if not date_str:
            return datetime.utcnow().isoformat() + "Z"
        try:
            # Check if already ISO
            if "T" in date_str:
                return date_str
            
            # Parse 'Feb 20, 2026 · 12:18 AM UTC'
            # Remove 'UTC' and parse
            clean_date = date_str.replace("UTC", "").strip()
            # Handle '·' separator
            if " · " in clean_date:
                dt = datetime.strptime(clean_date, "%b %d, %Y · %I:%M %p")
            else:
                # Fallback or other formats
                dt = datetime.utcnow()
            
            return dt.isoformat() + "Z"
        except Exception as e:
            logger.warning(f"Date parse error '{date_str}': {e}")
            return datetime.utcnow().isoformat() + "Z"

    def _normalize_apify_tweets(self, raw_items: list[dict]) -> list[dict]:
        """Normalize Apify actor output to our standard format."""
        normalized = []
        for item in raw_items:
            try:
                author = item.get("author", {}) or {}
                tweet = {
                    "tweet_id": str(item.get("id", "")),
                    "author_username": (
                        author.get("userName")
                        or author.get("username")
                        or item.get("authorUsername", "unknown")
                    ),
                    "author_name": (
                        author.get("name")
                        or author.get("displayName")
                        or ""
                    ),
                    "author_followers": int(
                        author.get("followers")
                        or author.get("followersCount")
                        or item.get("authorFollowers", 0)
                    ),
                    "author_verified": bool(
                        author.get("isVerified")
                        or author.get("isBlueVerified")
                        or False
                    ),
                    "full_text": (
                        item.get("text")
                        or item.get("full_text")
                        or item.get("fullText", "")
                    ),
                    "retweet_count": int(item.get("retweetCount", 0)),
                    "like_count": int(item.get("likeCount", 0)),
                    "reply_count": int(item.get("replyCount", 0)),
                    "quote_count": int(item.get("quoteCount", 0)),
                    "is_retweet": bool(item.get("isRetweet", False)),
                    "is_quote": bool(item.get("isQuote", False)),
                    "language": item.get("lang", "unknown"),
                    "tweet_created_at": item.get("createdAt"),
                }

                if tweet["full_text"].strip():
                    normalized.append(tweet)

            except Exception as e:
                logger.warning(f"Failed to normalize Apify tweet: {e}")
                continue

        return normalized

    def _deduplicate(self, tweets: list[dict]) -> list[dict]:
        """Remove tweets with duplicate text content."""
        seen = set()
        unique = []
        for t in tweets:
            key = t.get("full_text", "")[:100]  # Use first 100 chars as key
            if key and key not in seen:
                seen.add(key)
                unique.append(t)
        return unique
