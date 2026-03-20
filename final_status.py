import os
from web3 import Web3
from eth_account import Account
import json

# Correct addresses for Base Sepolia
OPG_TOKEN_ADDRESS = "0x240b09731D96979f50B2C649C9CE10FcF9C7987F"
PERMIT2_ADDRESS = "0x000000000022D473030F116ddeE9f6B43aC78BA3"
RPC_URL = "https://sepolia.base.org"

# Minimal ERC20 ABI
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
]

def check_status():
    print("--- Blockchain State Discovery ---")
    address = "0xBac5467880e18451428E7FD384E579C1e2CC0722"
    print(f"Checking address: {address}")
    
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    
    # 1. ETH Balance
    eth_balance = w3.eth.get_balance(address)
    print(f"💰 ETH: {w3.from_wei(eth_balance, 'ether'):.4f} ETH")
    
    # 2. $OPG Balance
    opg_contract = w3.eth.contract(address=Web3.to_checksum_address(OPG_TOKEN_ADDRESS), abi=ERC20_ABI)
    opg_balance = opg_contract.functions.balanceOf(address).call()
    print(f"💎 OPG: {opg_balance / 10**18:.2f} OPG")
    
    # 3. Permit2 Allowance
    allowance = opg_contract.functions.allowance(address, Web3.to_checksum_address(PERMIT2_ADDRESS)).call()
    print(f"🔐 Permit2 Allowance: {allowance / 10**18:.2f} OPG")
    
    print("\n--- VERDICT ---")
    if opg_balance == 0:
        print("❌ You have 0 $OPG. Please use the faucet: https://faucet.opengradient.ai/")
    elif allowance == 0:
        print("❌ You have $OPG but Permit2 allowance is 0. Please run the approval command.")
    else:
        print("✅ Everything looks good on-chain! If you still see 402, it might be a split-second indexing delay on the TEE server.")

if __name__ == "__main__":
    check_status()
