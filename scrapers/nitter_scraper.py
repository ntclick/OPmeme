# scrapers/nitter_scraper.py — Twitter/X Scraper via Nitter + Playwright
#
# Uses nitter.net (requires Playwright for JS rendering).
# Falls back to other instances if nitter.net fails.
#
# Requires: playwright + chromium
#   pip install playwright && playwright install chromium

import logging
import asyncio
import random
from typing import List, Dict
from datetime import datetime
from urllib.parse import quote

# Try importing Playwright (sync or async)
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    async_playwright = None
    PlaywrightTimeoutError = Exception

logger = logging.getLogger(__name__)

# Working Nitter instances (verified Feb 2026)
# nitter.net is the primary — it requires JS rendering via Playwright
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.cz",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]


class NitterScraper:
    """
    Scrapes tweets from Nitter instances using Playwright.
    
    Strategy:
    1. Try nitter.net first (most reliable, needs JS rendering)
    2. Rotate through other instances if nitter.net fails
    3. Parse tweets from HTML using BeautifulSoup
    """

    def __init__(self):
        if not async_playwright:
            logger.error("Playwright not installed! Run: pip install playwright && playwright install chromium")

    async def get_tweets(self, query: str, limit: int = 40) -> List[Dict]:
        """
        Scrape tweets for a given query using Playwright on Nitter.
        
        Args:
            query: Search query (e.g. "$PEPE OR #PEPE")
            limit: Maximum number of tweets to return
        """
        if not async_playwright:
            logger.error("Playwright not installed.")
            return []

        logger.info(f"NitterScraper: Starting scrape for '{query}' (limit={limit})...")

        async with async_playwright() as p:
            # Launch browser
            # Note: We use a slightly more distinct user agent to avoid basic blocks
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )

            tweets: List[Dict] = []
            
            # 1. Try nitter.net (needs JS)
            # 2. Try others if fail
            for instance in NITTER_INSTANCES:
                page = None
                try:
                    page = await context.new_page()
                    # Encode query properly
                    encoded_query = quote(query)
                    url = f"{instance}/search?f=tweets&q={encoded_query}"
                    
                    logger.info(f"NitterScraper: Trying {instance} -> {url}")
                    
                    response = await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    if not response:
                        logger.warning(f"NitterScraper: No response from {instance}")
                        continue
                        
                    # Check for Cloudflare/blocks
                    title = await page.title()
                    if any(x in title.lower() for x in ["cloudflare", "just a moment", "attention required"]):
                        logger.warning(f"NitterScraper: Blocked by Cloudflare on {instance}")
                        await page.close()
                        continue

                    # Wait for timeline items
                    try:
                        await page.wait_for_selector(".timeline-item", timeout=15000)
                    except PlaywrightTimeoutError:
                        content = await page.content()
                        if "No items found" in content:
                            logger.info(f"NitterScraper: No tweets found for '{query}' on {instance}")
                            await page.close()
                            # Valid empty result
                            break 
                        
                        logger.warning(f"NitterScraper: Timeline not loaded on {instance} (Title: {title})")
                        await page.close()
                        continue

                    # Parse content
                    html = await page.content()
                    extracted = self._parse_html(html, limit)
                    
                    if extracted:
                        tweets.extend(extracted)
                        logger.info(f"NitterScraper: Got {len(extracted)} tweets from {instance}")
                        await page.close()
                        break # Success
                    else:
                        logger.warning(f"NitterScraper: No structure found in {instance} HTML")

                except Exception as e:
                    logger.warning(f"NitterScraper: Error on {instance}: {type(e).__name__}: {e}")
                finally:
                    if page:
                        await page.close()

            await browser.close()
            return tweets[:limit]


    def _parse_html(self, html: str, limit: int) -> List[Dict]:
        """Parse Nitter HTML to extract tweet objects."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(".timeline-item")

        results = []
        for item in items:
            if len(results) >= limit:
                break

            try:
                # Skip pinned/promoted items
                if item.select_one(".pinned"):
                    continue

                # Text content
                content_div = item.select_one(".tweet-content")
                if not content_div:
                    continue
                text = content_div.get_text(strip=True)
                
                # Author info
                fullname_el = item.select_one(".fullname")
                username_el = item.select_one(".username")
                fullname = fullname_el.get_text(strip=True) if fullname_el else ""
                username = username_el.get_text(strip=True) if username_el else ""
                if username.startswith("@"):
                    username = username[1:]

                # Date
                date_el = item.select_one(".tweet-date a")
                timestamp = date_el.get("title", "") if date_el else ""
                
                # Stats
                stats = {"comments": 0, "retweets": 0, "quotes": 0, "likes": 0}
                stat_container = item.select_one(".tweet-stats")
                if stat_container:
                    # Simple stat parsing logic
                    for stat_item in stat_container.select(".tweet-stat"):
                        count_el = stat_item.select_one(".tweet-stat-count")
                        if not count_el: 
                            continue
                        count_val = count_el.get_text(strip=True).replace(",", "")
                        if not count_val.isdigit():
                            continue
                        count = int(count_val)
                        
                        icon = stat_item.select_one(".icon-container")
                        if icon:
                            # Class names often contain 'comment', 'retweet', 'quote', 'heart'
                            icon_html = str(icon)
                            if "comment" in icon_html: stats["comments"] = count
                            elif "retweet" in icon_html: stats["retweets"] = count
                            elif "quote" in icon_html: stats["quotes"] = count
                            elif "heart" in icon_html: stats["likes"] = count

                tweet = {
                    "id": "", # ID extraction logic omitted for brevity
                    "text": text,
                    "author": {
                        "userName": username,
                        "name": fullname,
                        "followers": 0, 
                        "verified": False,
                    },
                    "createdAt": timestamp,
                    "retweetCount": stats["retweets"],
                    "likeCount": stats["likes"],
                    "replyCount": stats["comments"],
                    "quoteCount": stats["quotes"],
                    "isRetweet": False,
                    "isQuote": False,
                    "lang": "unknown",
                    "url": "",
                }
                results.append(tweet)

            except Exception as e:
                continue

        return results

if __name__ == "__main__":
    # Test
    import sys
    logging.basicConfig(level=logging.INFO)
    scraper = NitterScraper()
    print(asyncio.run(scraper.get_tweets("$PEPE", 5)))
