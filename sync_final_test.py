import httpx
import os
import sys

# Apply patches from the source
sys.path.append(os.getcwd())
print("🚀 Importing OpenGradientAnalyzer (Applying Universal Patches)...")
from analysis.opengradient import OpenGradientAnalyzer

def main():
    print("--- Final Patched Sync Test ---")
    with httpx.Client() as client:
        for host in ["llm.opengradient.ai", "ogevmdevnet.opengradient.ai", "sepolia.base.org"]:
            print(f"Testing {host}...")
            try:
                # The patch should intercept this and redirect to the IP
                resp = client.get(f"https://{host}/", timeout=5.0)
                print(f"✅ {host} reached! Status: {resp.status_code}")
            except Exception as e:
                print(f"❌ {host} failed: {e}")

if __name__ == "__main__":
    main()
