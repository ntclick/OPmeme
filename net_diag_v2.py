import socket
import asyncio
import httpx

async def resolve_all():
    hosts = [
        "llm.opengradient.ai",
        "ogevmdevnet.opengradient.ai",
        "sepolia.base.org",
    ]
    
    print("--- Multi-Host Resolution Diagnostic ---")
    for host in hosts:
        print(f"\n🔍 Resolving {host}...")
        try:
            addr = socket.getaddrinfo(host, 443)
            print(f"✅ Resolved: {addr[0][4][0]}")
        except Exception as e:
            print(f"❌ Failed: {e}")

if __name__ == "__main__":
    asyncio.run(resolve_all())
