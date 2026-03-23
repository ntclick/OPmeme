# scrapers/evm_scraper.py — EVM Chain Support (Ethereum/Base/BSC)

import httpx
from typing import Optional, Dict, Any
import logging
import os

logger = logging.getLogger(__name__)


class EVMScraper:
    """
    Scraper for EVM chains (Ethereum, Base, BSC).
    Uses Etherscan/Basescan API for on-chain data.
    """
    
    ETHERSCAN_BASE = "https://api.etherscan.io/api"
    BASESCAN_BASE = "https://api.basescan.org/api"
    BSCSCAN_BASE = "https://api.bscscan.com/api"
    
    def __init__(self):
        self.api_key = os.getenv("ETHSCAN_API_KEY", "")
        if not self.api_key:
            logger.warning("⚠️ ETHSCAN_API_KEY not set - EVM calls will fail!")
    
    def _is_evm_address(self, address: str) -> bool:
        """Check if address is valid EVM format (0x...)."""
        if not address:
            return False
        return address.startswith("0x") and len(address) == 42
    
    async def _scan_request(
        self,
        base_url: str,
        module: str,
        action: str,
        address: str,
        **extra_params
    ) -> Optional[Dict[str, Any]]:
        """Make scan API request."""
        params = {
            "module": module,
            "action": action,
            "address": address,
            "apikey": self.api_key,
            **extra_params
        }
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(base_url, params=params, timeout=10.0)
                data = resp.json()
                if data.get("status") == "1":
                    return data.get("result", {})
                else:
                    logger.warning(f"Scan API error: {data.get('message')}")
                    return None
            except Exception as e:
                logger.warning(f"Scan API failed: {e}")
                return None
    
    async def get_token_info(self, address: str, chain: str = "base") -> Optional[Dict[str, Any]]:
        """Get token info from scan API."""
        if not self._is_evm_address(address):
            return None
        
        base_url = {
            "eth": self.ETHERSCAN_BASE,
            "base": self.BASESCAN_BASE,
            "bsc": self.BSCSCAN_BASE
        }.get(chain, self.BASESCAN_BASE)
        
        # Get contract ABI (token info)
        result = await self._scan_request(base_url, "contract", "getabi", address)
        if result:
            return {
                "address": address,
                "chain": chain,
                "is_contract": True,
                # Note: Full ABI parsing would require web3, simplified here
            }
        return None
    
    async def get_token_supply(self, address: str, chain: str = "base") -> Optional[int]:
        """Get total token supply."""
        if not self._is_evm_address(address):
            return None
        
        base_url = {
            "eth": self.ETHERSCAN_BASE,
            "base": self.BASESCAN_BASE,
            "bsc": self.BSCSCAN_BASE
        }.get(chain, self.BASESCAN_BASE)
        
        result = await self._scan_request(base_url, "stats", "tokensupply", address)
        if result:
            try:
                return int(result)
            except:
                pass
        return None
    
    def detect_chain(self, address: str) -> str:
        """Detect EVM chain from address (heuristic)."""
        if not self._is_evm_address(address):
            return "unknown"
        # Default to base for now, could be enhanced with chain detection logic
        return "base"


# Factory function
def create_evm_scraper() -> EVMScraper:
    return EVMScraper()
