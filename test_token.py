import sys
sys.path.insert(0, r'C:\Users\1phut\AppData\Local\Programs\Python\Python312\Lib\site-packages')
sys.path.insert(0, r'C:\Users\1phut\AppData\Roaming\Python\Python312\site-packages')

from dotenv import load_dotenv
load_dotenv()

import asyncio
from analysis.opengradient import OpenGradientAnalyzer

async def test():
    analyzer = OpenGradientAnalyzer()
    result = await analyzer.analyze_coin(
        ticker="TEST",
        tweets_summary=[],
        on_chain_data={"market_cap": 1000000, "holder_count": 100},
        scoring_result={"overall_score": 50}
    )
    print(result)

asyncio.run(test())
