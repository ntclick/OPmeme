import os
import sys
import asyncio

# Apply patches
sys.path.append(os.getcwd())
try:
    from analysis.opengradient import OpenGradientAnalyzer
except Exception as e:
    print(f"Warning: Could not import analyzer for patches: {e}")

import opengradient as og
from config import OPENGRADIENT_PRIVATE_KEY

async def fix_approval():
    print("--- Official Permit2 Approval Execution ---")
    pk = OPENGRADIENT_PRIVATE_KEY
    if not pk:
        print("❌ PK not found")
        return

    try:
        # Initialize SDK LLM
        llm = og.LLM(private_key=pk)
        
        print("\nAttempting llm.ensure_opg_approval(opg_amount=5.0)...")
        # Direct call to the SDK's built-in healing method
        # Note: This is a synchronous call in the current SDK version
        try:
            result = llm.ensure_opg_approval(opg_amount=5.0)
            print("\n✅ Execution Successful!")
            print(f"Allowance Before: {getattr(result, 'allowance_before', 'Unknown')}")
            print(f"Allowance After:  {getattr(result, 'allowance_after', 'Unknown')}")
            if getattr(result, 'tx_hash', None):
                print(f"🚀 Approval Transaction Sent: {result.tx_hash}")
            else:
                print("ℹ️ No transaction was necessary (sufficient allowance).")
        except Exception as sdk_e:
            print(f"\n❌ SDK Execution Failed: {sdk_e}")
            
    except Exception as e:
        print(f"❌ Initialization failed: {e}")

if __name__ == "__main__":
    asyncio.run(fix_approval())
