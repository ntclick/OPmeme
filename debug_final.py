
import asyncio
import json
import logging
from scrapers.solana_scraper import SolanaScraper
from analysis.scoring import calculate_holder_score, calculate_overall_score

# Silencing noisy logs
logging.basicConfig(level=logging.WARNING)

async def test():
    s = SolanaScraper()
    # The malformed address with 'l' and wrong casing from user screenshot
    token = '2ddooupqhpbzmfL2d3a7mngfuixzcvzlhxrnvqsypump'
    
    print(f"\n--- TESTING ADDRESS: {token} ---")
    
    # 1. Extraction phase
    print("[1] Fetching data...")
    info = await s.get_token_info(token)
    holders = await s.get_top_holders(token)
    
    if not info:
        print("FAILED: get_token_info returned None")
        return
    if not holders:
        print("FAILED: get_top_holders returned None")
        return
        
    print(f"Resolved Symbol: {info.get('symbol')}")
    print(f"Resolved Mint: {info.get('mint')}")
    print(f"Holder Count (Birdeye): {holders.get('total_holders')}")
    print(f"Top 10% Holders: {holders.get('top10_pct')}%")
    
    # 2. Scoring phase
    print("\n[2] Calculating scores...")
    h_score = calculate_holder_score(holders, info)
    print(f"Holder Score: {h_score['holder_score']} ({h_score['details']})")
    
    overall = calculate_overall_score(info, {'influencer_count': 0}, h_score)
    print(f"OVERALL SCORE: {overall['overall_score']}")
    print(f"Reasons: {overall['reasons']}")

if __name__ == '__main__':
    asyncio.run(test())
