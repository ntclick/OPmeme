import httpx
import asyncio
import ssl
import socket

async def raw_diag():
    ip = "13.59.207.188"
    domain = "llm.opengradient.ai"
    
    print(f"--- Raw Network Diagnostic ---")
    
    # Check 1: Socket resolution
    print(f"🔍 Checking socket resolution for {domain}...")
    try:
        addr = socket.getaddrinfo(domain, 443)
        print(f"✅ Resolved: {addr}")
    except Exception as e:
        print(f"❌ Resolution failed: {e}")
        
    # Check 2: Direct IP connection (HTTPS)
    print(f"\n🔍 Testing HTTPS connection to {ip}...")
    try:
        # Use verify=False to bypass SSL check for the IP
        async with httpx.AsyncClient(verify=False) as client:
            # TEE endpoint likely expects a POST or has a status page
            # We just want to see if we get a response (even 404 or 405)
            resp = await client.get(f"https://{ip}/v1/models", timeout=5.0)
            print(f"✅ Connected! Status: {resp.status_code}")
    except Exception as e:
        print(f"❌ Connection to {ip} failed: {e}")

    # Check 3: Other IP from user list
    ip2 = "3.15.214.21"
    print(f"\n🔍 Testing HTTPS connection to {ip2}...")
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(f"https://{ip2}/v1/models", timeout=5.0)
            print(f"✅ Connected! Status: {resp.status_code}")
    except Exception as e:
        print(f"❌ Connection to {ip2} failed: {e}")

if __name__ == "__main__":
    asyncio.run(raw_diag())
