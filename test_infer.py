"""Quick test: alpha.infer() on OpenGradient devnet (chain 10740)."""
import ssl
_orig = ssl.create_default_context
def _no_verify(*a, **kw):
    ctx = _orig(*a, **kw)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
ssl.create_default_context = _no_verify

import os, sys, logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))
from config import OPENGRADIENT_PRIVATE_KEY

# Set proxy (local only)
proxy = "http://uzkijfqe:1h8tfoyq41mt@31.59.20.176:6754/"
os.environ["HTTPS_PROXY"] = proxy
os.environ["HTTP_PROXY"] = proxy
os.environ["OG_PRIVATE_KEY"] = OPENGRADIENT_PRIVATE_KEY

import opengradient as og

client = og.Alpha(
    private_key=OPENGRADIENT_PRIVATE_KEY,
    rpc_url="https://ogevmdevnet.opengradient.ai",
)
print(f"Chain: {client._blockchain.eth.chain_id}")
bal = client._blockchain.eth.get_balance(client._wallet_account.address)
print(f"Balance: {bal / 1e18:.4f} ETH")

# Dummy 10x4 OHLC input
ohlc = [[1.0, 2.0, 3.0, 4.0]] * 10
model_cid = "hJD2Ja3akZFt1A2LT-D_1oxOCz_OtuGYw4V9eE1m39M"

print(f"\nRunning infer() with CID={model_cid[:20]}...")
try:
    result = client.infer(
        model_cid=model_cid,
        inference_mode=og.InferenceMode.VANILLA,
        model_input={"open_high_low_close": ohlc},
    )
    print(f"TX: {result.transaction_hash}")
    print(f"Output: {result.model_output}")
    if hasattr(result.model_output, 'numbers'):
        print(f"Numbers: {result.model_output.numbers}")
        for k, v in result.model_output.numbers.items():
            print(f"  {k} = {v} → float = {float(v.flat[0]) if hasattr(v, 'flat') else float(v)}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")

# Also test read_workflow_result
print("\n--- Testing read_workflow_result ---")
contract_30m = "0xD85BA71f5701dc4C5BDf9780189Db49C6F3708D2"
try:
    raw = client.read_workflow_result(contract_30m)
    print(f"Result: {raw}")
    print(f"Type: {type(raw)}")
    if hasattr(raw, 'numbers'):
        print(f"Numbers: {raw.numbers}")
    if hasattr(raw, '__dict__'):
        print(f"Dict: {raw.__dict__}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")

# Also test raw contract call with minimal ABI
print("\n--- Testing raw getInferenceResult ---")
from web3 import Web3
MINIMAL_ABI = [
    {"inputs": [], "name": "getInferenceResult", "outputs": [
        {"components": [
            {"name": "name", "type": "string"},
            {"name": "values", "type": "int256[]"},
            {"name": "decimals", "type": "uint8[]"},
            {"name": "shape", "type": "uint256[]"},
        ], "internalType": "struct ModelOutput[]", "name": "", "type": "tuple[]"}
    ], "stateMutability": "view", "type": "function"},
]
contract = client._blockchain.eth.contract(
    address=Web3.to_checksum_address(contract_30m),
    abi=MINIMAL_ABI,
)
try:
    raw = contract.functions.getInferenceResult().call()
    print(f"Raw: {raw}")
    if raw:
        for item in raw:
            name, values, decimals, shape = item
            val = values[0]
            dec = decimals[0] if decimals else 0
            print(f"  {name}: val={val}, dec={dec}, float={float(val) / (10**dec)}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
