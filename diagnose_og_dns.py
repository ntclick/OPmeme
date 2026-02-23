"""
Test script to diagnose OpenGradient DNS and connection issues.
Run this to check if the SDK can resolve hostnames and connect.
"""
import socket
import sys

def test_dns_resolution():
    """Test if we can resolve OpenGradient hostnames"""
    hosts_to_test = [
        "api.opengradient.ai",
        "llm.opengradient.ai", 
        "rpc.opengradient.ai",
        "opengradient.ai",
    ]
    
    print("=" * 60)
    print("DNS RESOLUTION TEST")
    print("=" * 60)
    
    for host in hosts_to_test:
        try:
            ip = socket.gethostbyname(host)
            print(f"✅ {host} -> {ip}")
        except socket.gaierror as e:
            print(f"❌ {host} -> FAILED: {e}")
    
    print()

def test_opengradient_sdk():
    """Test OpenGradient SDK initialization and connection"""
    print("=" * 60)
    print("OPENGRADIENT SDK TEST")
    print("=" * 60)
    
    try:
        import opengradient as og
        print(f"✅ OpenGradient SDK imported")
        
        # Try to check default endpoints
        try:
            # Look for default URLs in the module
            defaults = {}
            for attr in dir(og):
                if 'URL' in attr or 'ENDPOINT' in attr or 'SERVER' in attr:
                    val = getattr(og, attr)
                    if isinstance(val, str):
                        defaults[attr] = val
                        
            if defaults:
                print("\nDefault endpoints found:")
                for k, v in defaults.items():
                    print(f"  {k}: {v}")
            else:
                print("\n⚠️ No default endpoint constants found in module")
                
        except Exception as e:
            print(f"⚠️ Could not inspect defaults: {e}")
        
        # Try creating a client
        private_key = "0x1234567890123456789012345678901234567890123456789012345678901234"  # dummy
        try:
            client = og.Client(private_key=private_key)
            print(f"✅ Client created successfully")
            
            # Check if client has llm attribute
            if hasattr(client, 'llm'):
                print(f"✅ Client has llm attribute")
                llm = client.llm
                
                # Try to check server URLs
                if hasattr(llm, '_og_llm_server_url'):
                    print(f"  LLM Server URL: {llm._og_llm_server_url}")
                if hasattr(llm, '_og_llm_streaming_server_url'):
                    print(f"  Streaming URL: {llm._og_llm_streaming_server_url}")
            else:
                print("❌ Client missing llm attribute")
                
        except Exception as e:
            print(f"❌ Client creation failed: {e}")
            import traceback
            traceback.print_exc()
            
    except ImportError as e:
        print(f"❌ Cannot import opengradient: {e}")
        print("   Make sure you're in the virtual environment")
        return

def test_hosts_file():
    """Check hosts file for any OpenGradient entries"""
    print("\n" + "=" * 60)
    print("HOSTS FILE CHECK")
    print("=" * 60)
    
    hosts_path = r"C:\Windows\System32\drivers\etc\hosts"
    try:
        with open(hosts_path, 'r') as f:
            content = f.read()
            if 'opengradient' in content.lower():
                print("⚠️ Found OpenGradient entries in hosts file:")
                for line in content.split('\n'):
                    if 'opengradient' in line.lower():
                        print(f"  {line}")
            else:
                print("✅ No OpenGradient entries in hosts file (this is normal)")
    except Exception as e:
        print(f"⚠️ Could not read hosts file: {e}")

def main():
    test_dns_resolution()
    test_opengradient_sdk()
    test_hosts_file()
    
    print("\n" + "=" * 60)
    print("RECOMMENDATIONS")
    print("=" * 60)
    print("""
If DNS resolution fails:
1. Try using Google's DNS (8.8.8.8) or Cloudflare (1.1.1.1)
2. Check your internet connection
3. The endpoint might have changed - check OpenGradient docs

If DNS works but SDK still fails:
1. The SDK might be using wrong default URLs
2. Need to update SDK or patch the URLs
3. Check if firewall/antivirus is blocking Python
    """)

if __name__ == "__main__":
    main()
