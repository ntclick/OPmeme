from analysis.opengradient import OpenGradientAnalyzer
import asyncio

analyzer = OpenGradientAnalyzer()
print('Analyzer initialized:', analyzer._initialized)
if analyzer._init_error:
    print('Init error:', analyzer._init_error)

if analyzer._initialized:
    # Test inference
    async def test():
        result = await analyzer.analyze_coin(
            ticker="TEST",
            tweets_summary=[],
            on_chain_data={"market_cap": 1000000, "holder_count": 100},
            scoring_result={"overall_score": 50}
        )
        print('Inference result:', result)
    
    asyncio.run(test())
