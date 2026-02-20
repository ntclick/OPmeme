import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coincheckgo.utils.memsync import MemSyncClient
import time

def test_memsync():
    print("Testing MemSync Client...")
    client = MemSyncClient()
    
    if not client.is_enabled():
        print("❌ MemSync not enabled (check .env)")
        return

    # 1. Add Memory
    print("1. Adding memory...")
    success = client.add_memory(
        "TEST MEMORY: CoinCheckGo Sentinel is testing memory integration.",
        source="test-script"
    )
    if success:
        print("✅ Memory added.")
    else:
        print("❌ Failed to add memory.")

    # Wait for indexing
    print("Waiting 3s for indexing...")
    time.sleep(3)

    # 2. Search Memory
    print("2. Searching memory...")
    results = client.search("CoinCheckGo Sentinel")
    if results:
        print(f"✅ Found {len(results)} memories.")
        for m in results:
            print(f"   - {m.get('memory', '')[:50]}...")
    else:
        print("⚠️ No memories found (might need more time to index).")

if __name__ == "__main__":
    test_memsync()
