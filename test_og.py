"""Test OpenGradient SDK connectivity."""
import os, sys
os.chdir(os.path.dirname(__file__))

from config import OPENGRADIENT_PRIVATE_KEY as K
print(f"Key: {K[:10]}...")

import opengradient as og

# 1. Init client
c = og.Client(private_key=K)
print("✅ Client initialized")

# 2. List available models
print(f"Available models: {[m.name for m in og.TEE_LLM]}")

# 3. Try inference
import traceback
try:
    r = c.llm.chat(
        model=og.TEE_LLM.GPT_4O,
        messages=[{"role": "user", "content": "Say hello in 3 words"}],
        max_tokens=10,
    )
    print(f"✅ LLM Response: {r.chat_output}")
    print(f"✅ TX Hash: {getattr(r, 'payment_hash', None)}")
except Exception as e:
    print(f"❌ LLM Error: {type(e).__name__}: {e}")
    traceback.print_exc()
    
    # Try a different model
    print("\nTrying alternative models...")
    for model in og.TEE_LLM:
        try:
            r2 = c.llm.chat(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
            )
            print(f"  ✅ {model.name} works: {r2.chat_output}")
            break
        except Exception as e2:
            print(f"  ❌ {model.name}: {e2}")
