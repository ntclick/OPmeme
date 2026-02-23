
import opengradient as og
import inspect

print("--- og ---")
print(dir(og))

try:
    client = og.Client(private_key="0x" + "1"*64)
    print("\n--- client ---")
    print(dir(client))
    
    if hasattr(client, "alpha"):
        print("\n--- client.alpha ---")
        print(dir(client.alpha))
        
    if hasattr(client, "alphasense"):
        print("\n--- client.alphasense ---")
        print(dir(client.alphasense))

except Exception as e:
    print(f"Error initializing client: {e}")
