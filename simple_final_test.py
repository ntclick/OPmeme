import asyncio
import httpx
import os
import sys

# Apply patches from the source
sys.path.append(os.getcwd())
from analysis.opengradient import OpenGradientAnalyzer

async def main():
    print("--- Final Patched Test ---")
    async with httpx.AsyncClient() as client:
        try:
            # This should be intercepted by the patch in analysis/opengradient.py
            resp = await client.get("https://llm.opengradient.ai/", timeout=5.0)
            print(f"✅ llm.opengradient.ai reached via patch. Status: {resp.status_code}")
        except Exception as e:
            print(f"❌ Failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
