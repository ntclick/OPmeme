
import asyncio
import json
import requests
from scrapers.solana_scraper import SolanaScraper

async def test():
    s = SolanaScraper()
    
    # 1. Search LOREGUY on DexScreener
    print("\n[STEP 1] DexScreener Search for 'LOREGUY'")
    r = requests.get('https://api.dexscreener.com/latest/dex/search?q=LOREGUY')
    pairs = r.json().get('pairs', [])
    if pairs:
        print(f"Top Symbol: {pairs[0].get('baseToken', {}).get('symbol')}")
        print(f"Top Address: {pairs[0].get('baseToken', {}).get('address')}")
        print(f"Top Address Len: {len(pairs[0].get('baseToken', {}).get('address', ''))}")
    else:
        print("No pairs found for LOREGUY")

    # 2. Test User's Address from Screenshot
    token = '2ddooupqhpbzmfL2d3a7mngfuixzcvzlhxrnvqsypump'
    print(f"\n[STEP 2] Testing User Address: {token}")
    
    info = await s.get_token_info(token)
    if info:
        print(f"Resolved Mint: {info.get('mint')}")
        print(f"Resolved Mint Len: {len(info.get('mint', ''))}")
        print(f"Supply: {info.get('supply')}")
        
        holders = await s.get_top_holders(token)
        if holders:
            print(f"Total Holders (Birdeye): {holders.get('total_holders')}")
            print(f"Top 10% Holders: {holders.get('top10_pct')}")
            print(f"Top 20% Holders: {holders.get('top20_pct')}")
            print(f"Holders List Size: {len(holders.get('holders', []))}")
        else:
            print("HOLDERS FETCH FAILED")
    else:
        print("INFO FETCH FAILED")

if __name__ == '__main__':
    asyncio.run(test())
