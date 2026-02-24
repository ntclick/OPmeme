import opengradient as og
import os
from dotenv import load_dotenv
load_dotenv()

# Skip Firebase for now - just test basic SDK
PRIVATE_KEY = os.getenv("OPENGRADIENT_PRIVATE_KEY", "")
EMAIL = os.getenv("OPENGRADIENT_EMAIL", "")
PASSWORD = os.getenv("OPENGRADIENT_PASSWORD", "")

print("=== og attributes (before init) ===")
for attr in dir(og):
    if 'ensure' in attr.lower() or 'opg' in attr.lower() or 'wallet' in attr.lower():
        print(attr)

if PRIVATE_KEY and EMAIL and PASSWORD:
    print("\n=== Trying to import ensure_opg_approval ===")
    try:
        from opengradient.client.opg_token import ensure_opg_approval
        print("✅ ensure_opg_approval imported successfully")
        print(f"ensure_opg_approval type: {type(ensure_opg_approval)}")
    except Exception as e:
        print(f"❌ Failed to import ensure_opg_approval: {e}")
    
    print("\n=== Trying to import Permit2ApprovalResult ===")
    try:
        from opengradient.client.opg_token import Permit2ApprovalResult
        print("✅ Permit2ApprovalResult imported successfully")
        print(f"Permit2ApprovalResult type: {type(Permit2ApprovalResult)}")
    except Exception as e:
        print(f"❌ Failed to import Permit2ApprovalResult: {e}")
    
    print("\n=== Checking og module for client ===")
    print(f"hasattr(og, 'client'): {hasattr(og, 'client')}")
    
    print("\n=== Checking x402SettlementMode ===")
    if hasattr(og, 'x402SettlementMode'):
        print("og.x402SettlementMode attributes:")
        for attr in dir(og.x402SettlementMode):
            if not attr.startswith('_'):
                print(f"  {attr}")
    else:
        print("❌ No x402SettlementMode found")
    
    print("\n=== All public attributes ===")
    for attr in dir(og):
        if not attr.startswith('_'):
            print(f"  {attr}")
    
    print("\n=== Checking for llm_chat ===")
    print(f"hasattr(og, 'llm_chat'): {hasattr(og, 'llm_chat')}")
    print(f"hasattr(og, 'llm_completion'): {hasattr(og, 'llm_completion')}")
    
    # Try to init and check again
    print("\n=== Trying to init with private key only ===")
    try:
        og.init(private_key=PRIVATE_KEY)
        print("✅ og.init() successful")
        
        print(f"\nAfter init - og.global_client: {getattr(og, 'global_client', 'NOT FOUND')}")
        print(f"After init - hasattr(og, 'llm_chat'): {hasattr(og, 'llm_chat')}")
        
        # Check client attributes
        if hasattr(og, 'global_client') and og.global_client:
            client = og.global_client
            print(f"\nClient type: {type(client)}")
            print(f"Client attributes with 'llm':")
            for attr in dir(client):
                if 'llm' in attr.lower():
                    print(f"  {attr}")
            
            if hasattr(client, 'llm'):
                print(f"\nclient.llm type: {type(client.llm)}")
                print(f"client.llm attributes:")
                for attr in dir(client.llm):
                    if not attr.startswith('_'):
                        print(f"  {attr}")
    except Exception as e:
        print(f"❌ og.init() failed: {e}")
    
else:
    print("Missing credentials")
