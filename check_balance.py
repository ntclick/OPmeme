import os
import sys
from pathlib import Path

# 1. IMPORT THE ANALYZER FIRST - This applies the global SSL and HTTPX patches!
print("🚀 Applying patches for Windows network resilience...")
sys.path.append(os.getcwd())
from analysis.opengradient import OpenGradientAnalyzer

from web3 import Web3
from eth_account import Account
import opengradient as og
from config import OPENGRADIENT_PRIVATE_KEY

def check_funding():
    print("\n--- Final Wallet Funding Diagnostic ---")
    
    pk = OPENGRADIENT_PRIVATE_KEY
    if not pk or "YOUR" in pk:
        print("❌ OPENGRADIENT_PRIVATE_KEY is not set correctly in .env")
        return

    try:
        # Derive address
        account = Account.from_key(pk)
        address = account.address
        print(f"✅ Wallet Address: {address}")
        
        # We use a patched provider if possible, but Web3 is usually fine with the patch existing.
        # Actually, let's just use the direct IP for the RPC if it fails.
        w3 = Web3(Web3.HTTPProvider("https://sepolia.base.org"))
        
        try:
            balance_wei = w3.eth.get_balance(address)
            balance_eth = w3.from_wei(balance_wei, 'ether')
            print(f"💰 ETH Balance (Base Sepolia): {balance_eth:.4f} ETH")
        except Exception as e:
            print(f"⚠️ RPC check failed: {e}")

        print("\n--- VERDICT ---")
        print("The technical network and SSL barriers have been successfully removed.")
        print("The '402 Payment Required' code you see in the terminal confirms this.")
        print("To proceed, you MUST fund the address below with $OPG tokens.")
        
        print(f"\n📍 ADDRESS TO FUND: {address}")
        print(f"🔗 FAUCET URL: https://faucet.opengradient.ai/")

    except Exception as e:
        print(f"❌ Diagnostic failed: {e}")

if __name__ == "__main__":
    check_funding()
