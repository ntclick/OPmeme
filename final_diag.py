import logging
import asyncio
import os
import sys

# Ensure we can import from the current directory
sys.path.append(os.getcwd())

# 1. IMPORT THE ANALYZER FIRST - This applies the global SSL and HTTPX patches!
print("🚀 Importing OpenGradientAnalyzer to apply global patches...")
from analysis.opengradient import OpenGradientAnalyzer

import socket
import ssl
import httpx
from web3 import Web3

# Domain Mapping
DNS_MAP = {
    "llm.opengradient.ai": "13.59.207.188",
    "ogevmdevnet.opengradient.ai": "3.14.94.135",
    "sepolia.base.org": "104.18.40.153",
}

async def diag():
    print("\n--- Final Patched Connectivity Diagnostic ---")
    
    # Check 1: Socket Resolution
    print(f"🔍 Testing standard socket resolution...")
    for host in DNS_MAP.keys():
        try:
            addr = socket.getaddrinfo(host, 443)
            print(f"✅ {host} -> {addr[0][4][0]}")
        except Exception as e:
            print(f"❌ {host} resolution failed: {e}")

    # Check 2: Patched HTTPX connectivity (Async)
    print(f"\n🔍 Testing PATCHED ASYNC HTTPX connectivity...")
    async with httpx.AsyncClient() as client:
        for host in DNS_MAP.keys():
            print(f"Testing {host}...")
            try:
                resp = await client.get(f"https://{host}/", timeout=5.0)
                print(f"✅ {host} reachable! Status: {resp.status_code}")
            except Exception as e:
                print(f"❌ {host} unreachable via async httpx: {e}")

    # Check 3: Patched HTTPX connectivity (Sync)
    print(f"\n🔍 Testing PATCHED SYNC HTTPX connectivity...")
    with httpx.Client() as client:
        for host in DNS_MAP.keys():
            print(f"Testing {host}...")
            try:
                resp = client.get(f"https://{host}/", timeout=5.0)
                print(f"✅ {host} reachable! Status: {resp.status_code}")
            except Exception as e:
                print(f"❌ {host} unreachable via sync httpx: {e}")

    # Check 4: Web3
    print(f"\n🔍 Testing Web3 connectivity...")
    w3 = Web3(Web3.HTTPProvider("https://sepolia.base.org"))
    try:
        # Use a simple call that doesn't require async context if possible
        connected = w3.is_connected()
        if connected:
            print(f"✅ Web3 connected to Base Sepolia")
        else:
            print(f"❌ Web3 NOT connected")
    except Exception as e:
        print(f"❌ Web3 failure: {e}")

if __name__ == "__main__":
    asyncio.run(diag())
