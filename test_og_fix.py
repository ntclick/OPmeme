"""
Test OpenGradient connection after DNS fix.
Run this to verify the SDK can connect using IP addresses.
"""
import asyncio
import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def test_og_connection():
    """Test if OpenGradient SDK can connect with IP-based URLs"""
    print("=" * 60)
    print("TESTING OPENGRADIENT CONNECTION WITH IP FIX")
    print("=" * 60)
    
    try:
        from analysis.opengradient import OpenGradientAnalyzer
        
        print("\n1. Initializing OpenGradientAnalyzer...")
        analyzer = OpenGradientAnalyzer()
        
        if not analyzer._initialized:
            print("❌ FAILED: OpenGradientAnalyzer not initialized")
            print("   Check logs above for connection errors")
            return False
            
        print("✅ OpenGradientAnalyzer initialized successfully")
        
        # Check client attributes
        if analyzer._client:
            print("\n2. Checking client configuration...")
            
            # Check LLM URLs
            llm = analyzer._client.llm
            if hasattr(llm, '_og_llm_server_url'):
                url = llm._og_llm_server_url
                print(f"   LLM Server URL: {url}")
                if '3.16.84.142' in url:
                    print("   ✅ Using IP address (bypassing DNS)")
                else:
                    print("   ⚠️ Using hostname (may fail DNS)")
            
            # Test simple inference
            print("\n3. Testing simple inference...")
            try:
                result = analyzer._client.llm.chat(
                    model=analyzer._og.TEE_LLM.GPT_4O,
                    messages=[{"role": "user", "content": "Say 'OpenGradient connected' if you receive this."}],
                    max_tokens=50,
                    temperature=0.0
                )
                
                if result and result.chat_output:
                    output = result.chat_output.get('content', '') if isinstance(result.chat_output, dict) else str(result.chat_output)
                    print(f"   ✅ Inference successful!")
                    print(f"   Response: {output[:100]}")
                    
                    if result.transaction_hash:
                        print(f"   TX Hash: {result.transaction_hash}")
                    return True
                else:
                    print("❌ Inference returned empty result")
                    return False
                    
            except Exception as e:
                print(f"❌ Inference failed: {e}")
                import traceback
                traceback.print_exc()
                return False
        else:
            print("❌ Client not available")
            return False
            
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_og_connection())
    
    print("\n" + "=" * 60)
    if success:
        print("✅ ALL TESTS PASSED - OpenGradient is working!")
        print("=" * 60)
        sys.exit(0)
    else:
        print("❌ TESTS FAILED - Check errors above")
        print("=" * 60)
        sys.exit(1)
