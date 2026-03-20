import os
import sys
from pathlib import Path

# Apply patches from the source
sys.path.append(os.getcwd())
from analysis.opengradient import OpenGradientAnalyzer

import opengradient as og
from config import OPENGRADIENT_PRIVATE_KEY
from eth_account import Account

def check_sdk_address():
    print("--- SDK Wallet Discovery ---")
    pk = OPENGRADIENT_PRIVATE_KEY
    if not pk:
        print("❌ PK not found")
        return

    # 1. Native derivation
    native_addr = Account.from_key(pk).address
    print(f"Native eth_account address: {native_addr}")

    # 2. SDK level - If the SDK has a way to show it
    try:
        # Most SDKs that use PK have an account object
        llm = og.LLM(private_key=pk)
        # Check if llm has an address or account
        print(f"SDK LLM Object Created. (Checking for address...)")
        # We'll just confirm if it matches
        # The SDK usually has a property like ._account
        if hasattr(llm, "_account"):
            print(f"SDK _account.address: {llm._account.address}")
        elif hasattr(llm, "address"):
            print(f"SDK llm.address: {llm.address}")
        else:
            print("SDK doesn't expose address directly, using native check.")
    except Exception as e:
        print(f"❌ SDK check failed: {e}")

if __name__ == "__main__":
    check_sdk_address()
