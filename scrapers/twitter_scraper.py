# scrapers/twitter_scraper.py — Twitter Scraper via Apify actor (no Playwright)

from apify_client import ApifyClient
from config import APIFY_API_TOKEN, APIFY_TWITTER_ACTOR_ID
from typing import Optional
import logging
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)


class TwitterScraper:
    """Twitter/X scraper using Apify actor only."""

    def __init__(self):
        self.actor_id = APIFY_TWITTER_ACTOR_ID
        self.client = ApifyClient(APIFY_API_TOKEN) if APIFY_API_TOKEN else None
        if not APIFY_API_TOKEN:
            logger.warning("APIFY_API_TOKEN not configured - twitter scraping disabled")

    def scrape_by_ticker(
        self,
        ticker: str,
        max_tweets: int = 40,
        language: Optional[str] = None,
    ) -> list[dict]:
        """Scrape tweets for a ticker/query using Apify actor."""
        if not self.client:
            return []

        queries = self._build_queries(ticker)
        if not queries:
            return []

        raw_items = self._fetch_from_apify(queries, max_tweets=max_tweets, language=language)
        if not raw_items:
            return []

        normalized = self._normalize_apify_tweets(raw_items)
        deduplicated = self._deduplicate(normalized)
        deduplicated.sort(
            key=lambda t: t["like_count"] + t["retweet_count"],
            reverse=True,
        )
        return deduplicated[:max_tweets]

    def _build_queries(self, ticker_or_query: str) -> list[str]:
        """Build search queries from ticker or raw query input."""
        raw = (ticker_or_query or "").strip()
        if not raw:
            return []

        if any(token in raw for token in [" OR ", " AND ", "from:", "since:", "until:", "(", ")"]):
            return [raw]

        base = raw.strip("$#")
        if not base:
            return []

        queries = [f"${base}", f"#{base}", base]
        return list(dict.fromkeys(queries))

    def _fetch_from_apify(self, queries: list[str], max_tweets: int, language: Optional[str]) -> list[dict]:
        """Run Apify actor with a few compatible input variants."""
        input_variants = self._build_input_variants(queries, max_tweets, language)

        for idx, run_input in enumerate(input_variants, start=1):
            try:
                logger.info(f"[Apify] Run {idx}/{len(input_variants)} actor={self.actor_id} input_keys={list(run_input.keys())}")
                run = self.client.actor(self.actor_id).call(run_input=run_input, timeout_secs=180)
                if not run:
                    logger.warning("[Apify] Empty run response")
                    continue

                dataset_id = run.get("defaultDatasetId")
                if not dataset_id:
                    logger.warning("[Apify] No defaultDatasetId in run result")
                    continue

                items: list[dict] = []
                for item in self.client.dataset(dataset_id).iterate_items():
                    items.append(item)
                    if len(items) >= max(50, max_tweets * 3):
                        break

                if items:
                    logger.info(f"[Apify] Collected {len(items)} raw tweets")
                    return items

                logger.warning("[Apify] Run completed but returned no items")
            except Exception as e:
                logger.warning(f"[Apify] Run variant {idx} failed: {e}")

        return []

    def _build_input_variants(self, queries: list[str], max_tweets: int, language: Optional[str]) -> list[dict]:
        """Build several actor input schemas to maximize compatibility."""
        max_items = max(1, min(max_tweets, 200))

        variant_1 = {
            "searchTerms": queries,
            "maxItems": max_items,
            "sort": "Latest",
        }
        if language:
            variant_1["tweetLanguage"] = language

        variant_2 = {
            "queries": queries,
            "maxItems": max_items,
        }
        if language:
            variant_2["tweetLanguage"] = language

        variant_3 = {
            "query": " OR ".join(queries),
            "maxItems": max_items,
        }
        if language:
            variant_3["lang"] = language

        return [variant_1, variant_2, variant_3]

    def _to_int(self, value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)

        text = str(value).strip().replace(",", "")
        if not text:
            return 0

        multiplier = 1
        suffix = text[-1].lower()
        if suffix == "k":
            multiplier = 1_000
            text = text[:-1]
        elif suffix == "m":
            multiplier = 1_000_000
            text = text[:-1]
        elif suffix == "b":
            multiplier = 1_000_000_000
            text = text[:-1]

        try:
            return int(float(text) * multiplier)
        except Exception:
            return 0

    def _normalize_apify_tweets(self, raw_items: list[dict]) -> list[dict]:
        """Normalize Apify actor output to our standard format."""
        normalized = []
        for item in raw_items:
            try:
                author = item.get("author") or item.get("user") or {}
                if not isinstance(author, dict):
                    author = {}

                text = (
                    item.get("text")
                    or item.get("full_text")
                    or item.get("fullText")
                    or item.get("tweetText")
                    or ""
                )
                text = str(text).strip()
                if not text:
                    continue

                author_username = (
                    author.get("userName")
                    or author.get("username")
                    or author.get("screen_name")
                    or author.get("handle")
                    or item.get("authorUsername")
                    or item.get("username")
                    or "unknown"
                )

                tweet_created_at = (
                    item.get("createdAt")
                    or item.get("timestamp")
                    or item.get("date")
                    or datetime.utcnow().isoformat()
                )

                tweet_id = str(item.get("id") or item.get("tweetId") or item.get("tweet_id") or "")
                if not tweet_id:
                    unique_str = f"{text}|{author_username}|{tweet_created_at}"
                    tweet_id = hashlib.md5(unique_str.encode()).hexdigest()

                tweet = {
                    "tweet_id": tweet_id,
                    "author_username": author_username,
                    "author_name": (
                        author.get("name")
                        or author.get("displayName")
                        or item.get("authorName")
                        or ""
                    ),
                    "author_followers": self._to_int(
                        author.get("followers")
                        or author.get("followersCount")
                        or item.get("authorFollowers", 0)
                    ),
                    "author_verified": bool(
                        author.get("isVerified")
                        or author.get("isBlueVerified")
                        or author.get("verified")
                        or item.get("authorVerified")
                        or item.get("verified")
                        or False
                    ),
                    "full_text": text,
                    "retweet_count": self._to_int(item.get("retweetCount") or item.get("retweets") or item.get("retweet_count")),
                    "like_count": self._to_int(item.get("likeCount") or item.get("favoriteCount") or item.get("likes")),
                    "reply_count": self._to_int(item.get("replyCount") or item.get("replies")),
                    "quote_count": self._to_int(item.get("quoteCount") or item.get("quotes")),
                    "is_retweet": bool(item.get("isRetweet") or item.get("retweeted") or False),
                    "is_quote": bool(item.get("isQuote") or item.get("quoted") or False),
                    "language": item.get("lang") or item.get("language") or "unknown",
                    "tweet_created_at": tweet_created_at,
                }

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
