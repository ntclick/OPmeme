
import httpx
import asyncio

async def main():
    addr = "9G41T3N4XUV8UFCWSODQZ4LFLKE2FWWU3W85M8PLLR7M"
    print(f"Checking {addr}...")
    
    async with httpx.AsyncClient() as client:
        # Token endpoint
        try:
            r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{addr}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                     print(f"Token Endpoint: Found symbol {pairs[0].get('baseToken', {}).get('symbol')}")
                else:
                     print("Token Endpoint: No pairs")
        except Exception as e:
            print(f"Token Error: {e}")

        # Pair endpoint
        try:
            r = await client.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{addr}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                     print(f"Pair Endpoint: Found symbol {pairs[0].get('baseToken', {}).get('symbol')}")
                else:
                     print("Pair Endpoint: No pairs")
        except Exception as e:
            print(f"Pair Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
