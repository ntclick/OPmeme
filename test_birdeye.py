import asyncio
import os
import sys

from scrapers.birdeye_client import create_birdeye_client
from scrapers.solana_scraper import SolanaScraper

async def test_birdeye():
    from config import BIRDEYE_API_KEY
    c = create_birdeye_client(BIRDEYE_API_KEY)
    
    # Test 1: overview
    mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    overview = await c.get_token_overview(mint)
    print("Overview keys:", list(overview.keys()) if overview else "None")
    
    # Test 2: extractor
    scores = c.extract_scores_from_overview(overview)
    print("Scores extracted:", list(scores.keys()) if scores else "None")
    
    # Test 3: solana scraper backwards compat for holders
    s = SolanaScraper()
    holders = await s.get_top_holders(mint, top_n=5)
    print("Top holders:", getattr(holders, 'keys', lambda: None)())

if __name__ == "__main__":
    asyncio.run(test_birdeye())
