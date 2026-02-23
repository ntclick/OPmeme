import asyncio
import logging
from analysis.opengradient import OpenGradientAnalyzer

logging.basicConfig(level=logging.INFO)

import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def test():
    analyzer = OpenGradientAnalyzer()
    res = await analyzer.analyze_coin(
        "PENGUIN",
        [],
        {"token_info": {"marketCap": 1000000}},
        {"social_score": 50, "bot_score": 50, "holder_score": 50}
    )
    print("Agent Result:")
    import pprint
    pprint.pprint(res)

if __name__ == "__main__":
    asyncio.run(test())
