
import json
import http.client
import urllib.parse

def test_rpc():
    # Verified address from DexScreener page (44 chars)
    token = "2DDooUpqhPbZMFL2d3a7MNGfuixzCvZLHxRNvqSYpump"
    api_key = "fb2a828b-5a11-4c38-b12a-ee03e43079bc"
    
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    parsed_url = urllib.parse.urlparse(url)
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [token, {"encoding": "jsonParsed"}]
    }
    
    conn = http.client.HTTPSConnection(parsed_url.netloc)
    headers = {"Content-Type": "application/json"}
    
    path = f"{parsed_url.path}?{parsed_url.query}"
    conn.request("POST", path, json.dumps(payload), headers)
    
    res = conn.getresponse()
    print(f"AccountInfo Status: {res.status}")
    data = json.loads(res.read().decode())
    print(json.dumps(data, indent=2))

    # Test getTokenLargestAccounts
    payload_holders = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [token]
    }
    conn.request("POST", path, json.dumps(payload_holders), headers)
    res_h = conn.getresponse()
    print(f"Holders Status: {res_h.status}")
    data_h = json.loads(res_h.read().decode())
    print(json.dumps(data_h, indent=2))

if __name__ == "__main__":
    test_rpc()
