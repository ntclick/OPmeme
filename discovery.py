import os
from dotenv import load_dotenv
from eth_account import Account
import sys

# Ensure we are in the right dir
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def discover():
    # Load .env explicitly
    load_dotenv()
    
    print("--- Environment PK Discovery ---")
    vars_to_check = ["OPENGRADIENT_PRIVATE_KEY", "OG_PRIVATE_KEY", "PRIVATE_KEY"]
    
    for v in vars_to_check:
        val = os.getenv(v)
        if val:
            try:
                # Basic cleaning
                clean_val = val.strip()
                if not clean_val.startswith("0x") and len(clean_val) == 64:
                    clean_val = "0x" + clean_val
                
                acc = Account.from_key(clean_val)
                print(f"{v}: {acc.address}")
                print(f"  (PK starts with: {clean_val[:10]}...)")
            except Exception as e:
                print(f"{v}: INVALID PK ({e})")
        else:
            print(f"{v}: NOT SET")

if __name__ == "__main__":
    discover()
