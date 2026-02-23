
import asyncio
import logging
import json
from scrapers.solana_scraper import SolanaScraper

# Enable logging
logging.basicConfig(level=logging.INFO)

async def test():
    s = SolanaScraper()
    # Address from user screenshot (Pump.fun pair address)
    token = '2ddooupqhpbzmfL2d3a7mngfuixzcvzlhxrnvqsypump'
    
    print(f"\n--- TESTING ADDRESS: {token} ---")
    
    print("\n[STEP 1] Fetching Token Info...")
    info = await s.get_token_info(token)
    if info:
        print(json.dumps(info, indent=2))
    else:
        print("INFO FAILED (None)")
    
    print("\n[STEP 2] Fetching Top Holders...")
    holders = await s.get_top_holders(token)
    if holders:
        print(f"Total Holders (Birdeye): {holders.get('total_holders')}")
        print(f"Top 10%: {holders.get('top10_pct')}")
        print(f"Holders list size: {len(holders.get('holders', []))}")
        if holders.get('holders'):
            print(f"Top 1 Holder %: {holders['holders'][0].get('percentage')}")
    else:
        print("HOLDERS FAILED (None)")

if __name__ == '__main__':
    asyncio.run(test())
