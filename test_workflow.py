"""Test: workflow + infer on OpenGradient devnet."""
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
sys.path.insert(0, os.path.dirname(__file__))
from config import OPENGRADIENT_PRIVATE_KEY

os.environ["OG_PRIVATE_KEY"] = OPENGRADIENT_PRIVATE_KEY

# Proxy bypass
proxy = "http://uzkijfqe:1h8tfoyq41mt@31.59.20.176:6754/"
for k in ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"]:
    os.environ[k] = proxy

# --- Apply Alpha.infer() patch (SDK 0.9.3 bug: event not in logs) ---
from analysis.forecast import _patch_alpha_infer  # already called at import

import opengradient as og
from web3 import Web3
from web3.logs import DISCARD

CONTRACT = "0xD5629A5b95dde11e4B5772B5Ad8a13B933e33845"

alpha = og.Alpha(private_key=OPENGRADIENT_PRIVATE_KEY)
print(f"Chain: {alpha._blockchain.eth.chain_id}")
print(f"Wallet: {alpha._wallet_account.address}")
bal = alpha._blockchain.eth.get_balance(alpha._wallet_account.address)
print(f"Balance: {bal / 1e18:.6f} ETH")

# --- Test 1: run_workflow + parse events ---
print(f"\n=== run_workflow {CONTRACT[:12]}... ===")
try:
    WORKFLOW_ABI = [
        {"inputs": [], "name": "run", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
        {"inputs": [], "name": "getInferenceResult", "outputs": [
            {"components": [
                {"name": "name", "type": "string"},
                {"name": "values", "type": "int256[]"},
                {"name": "decimals", "type": "uint8[]"},
                {"name": "shape", "type": "uint256[]"},
            ], "internalType": "struct ModelOutput[]", "name": "", "type": "tuple[]"}
        ], "stateMutability": "view", "type": "function"},
    ]
    contract = alpha._blockchain.eth.contract(
        address=Web3.to_checksum_address(CONTRACT),
        abi=WORKFLOW_ABI,
    )
    nonce = alpha._blockchain.eth.get_transaction_count(alpha._wallet_account.address, "pending")
    tx = contract.functions.run().build_transaction({
        "from": alpha._wallet_account.address,
        "nonce": nonce,
        "gas": 9_000_000,
        "gasPrice": alpha._blockchain.eth.gas_price,
        "chainId": alpha._blockchain.eth.chain_id,
    })
    signed = alpha._wallet_account.sign_transaction(tx)
    tx_hash = alpha._blockchain.eth.send_raw_transaction(signed.raw_transaction)
    print(f"TX sent: {tx_hash.hex()[:20]}...")
    receipt = alpha._blockchain.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    print(f"Mined! block={receipt['blockNumber']} status={receipt['status']} gas={receipt['gasUsed']}")
    print(f"Logs count: {len(receipt['logs'])}")

    # Dump all raw logs to understand the contract's events
    for i, log in enumerate(receipt['logs']):
        print(f"  Log[{i}]: address={log['address'][:12]}... topics={len(log['topics'])} data_len={len(log['data'])}")

    # Try getInferenceResult
    try:
        raw = contract.functions.getInferenceResult().call()
        print(f"getInferenceResult: {raw}")
    except Exception as e:
        print(f"getInferenceResult failed: {e}")

    # Try via precompile (same fallback as forecast.py patch)
    from opengradient.client.alpha import PRECOMPILE_CONTRACT_ADDRESS
    precompile = alpha._blockchain.eth.contract(
        address=Web3.to_checksum_address(PRECOMPILE_CONTRACT_ADDRESS),
        abi=alpha.precompile_abi,
    )
    precompile_logs = precompile.events.ModelInferenceEvent().process_receipt(receipt, errors=DISCARD)
    if precompile_logs:
        inference_id = precompile_logs[0]["args"]["inferenceID"]
        print(f"Precompile inferenceID: {inference_id}")
        result = alpha._get_inference_result_from_node(inference_id, og.InferenceMode.VANILLA)
        print(f"Node API result: {result}")
    else:
        print("No precompile logs found")

except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")

# --- Test 2: direct infer() with patch ---
print(f"\n=== alpha.infer() (patched) ===")
MODEL_CID = "hJD2Ja3akZFt1A2LT-D_1oxOCz_OtuGYw4V9eE1m39M"
ohlc = [[1.0, 2.0, 3.0, 4.0]] * 10
try:
    result = alpha.infer(
        model_cid=MODEL_CID,
        inference_mode=og.InferenceMode.VANILLA,
        model_input={"open_high_low_close": ohlc},
    )
    print(f"TX: {result.transaction_hash}")
    print(f"Output: {result.model_output}")
    if hasattr(result.model_output, 'numbers'):
        for k, v in result.model_output.numbers.items():
            val = float(v.flat[0]) if hasattr(v, 'flat') else float(v)
            print(f"  {k} = {val}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
