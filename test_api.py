import requests
import json

url = "http://127.0.0.1:8000/api/analyze"
payload = {"ticker": "BONK"}

try:
    resp = requests.post(url, json=payload, timeout=90)
    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(json.dumps(data, indent=2))
except Exception as e:
    print(f"Error: {e}")
