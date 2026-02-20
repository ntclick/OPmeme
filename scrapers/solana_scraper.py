# scrapers/solana_scraper.py — Solana RPC / Helius API client

import httpx
from config import SOLANA_RPC_URL, HELIUS_API_KEY
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SolanaScraper:
    """Fetch on-chain data for Solana tokens."""

    def __init__(self):
        # Prefer Helius if key is available, fallback to public RPC
        if HELIUS_API_KEY:
            self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
            self.helius_api = f"https://api.helius.xyz/v0"
        else:
            self.rpc_urls = [
                "https://api.mainnet-beta.solana.com",
                "https://rpc.ankr.com/solana",
                "https://solana-api.projectserum.com",
            ]
            self.current_rpc_index = 0
            self.helius_api = None

    def _get_rpc_url(self):
        """Rotate RPC URL on failure."""
        url = self.rpc_urls[self.current_rpc_index]
        self.current_rpc_index = (self.current_rpc_index + 1) % len(self.rpc_urls)
        return url


    async def get_token_symbol(self, mint_address: str) -> Optional[str]:
        """Resolve token symbol via DexScreener API (Token or Pair)."""
        async with httpx.AsyncClient() as client:
            # 1. Try as Token Address
            try:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_address}"
                resp = await client.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        return pairs[0].get("baseToken", {}).get("symbol")
            except Exception as e:
                logger.warning(f"DexScreener token fetch failed: {e}")

            # 2. Fallback: Try as Pair Address (Solana)
            try:
                url_pair = f"https://api.dexscreener.com/latest/dex/pairs/solana/{mint_address}"
                resp = await client.get(url_pair, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    pair = data.get("pair") # Single pair object or pairs list? API docs say plural for /pairs path but let's check structure
                    # Actual DexScreener /pairs endpoint returns { schema: ..., pairs: [...] } usually
                    # But /pairs/chainId/pairAddresses returns { pairs: [...] }
                    pairs = data.get("pairs", [])
                    if pairs:
                         return pairs[0].get("baseToken", {}).get("symbol")
            except Exception as e:
                logger.warning(f"DexScreener pair fetch failed: {e}")
                
        return None

    async def get_token_info(self, mint_address: str) -> Optional[dict]:
        """
        Get basic token information from Solana.

        Returns:
        {
            "mint": "address...",
            "supply": 1000000000,
            "decimals": 9,
            "mint_authority": "address..." or None,
            "freeze_authority": "address..." or None,
            "is_mint_renounced": True/False,
        }
        """
        async with httpx.AsyncClient() as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    mint_address,
                    {"encoding": "jsonParsed"},
                ],
            }

            # Retry with rotation
            for _ in range(3):
                try:
                    current_rpc = self._get_rpc_url()
                    resp = await client.post(current_rpc, json=payload, timeout=10)
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except Exception:
                    continue
            else:
                 # Final attempt or fail
                 return None

            account_data = data.get("result", {}).get("value", {})
            if not account_data:
                return None

            parsed = (
                account_data
                .get("data", {})
                .get("parsed", {})
                .get("info", {})
            )

            return {
                "mint": mint_address,
                "supply": int(parsed.get("supply", 0)),
                "decimals": parsed.get("decimals", 9),
                "mint_authority": parsed.get("mintAuthority"),
                "freeze_authority": parsed.get("freezeAuthority"),
                "is_mint_renounced": parsed.get("mintAuthority") is None,
            }


    async def get_top_holders(
        self, mint_address: str, top_n: int = 20
    ) -> Optional[dict]:
        """
        Get top holders of a token.

        Algorithm:
        1. Call getTokenLargestAccounts (Solana RPC native)
        2. Calculate each holder's % of total supply
        3. Compute concentration metrics

        Returns:
        {
            "total_holders": 5000,       # (estimated, needs Helius)
            "top10_pct": 45.2,
            "top20_pct": 62.1,
            "largest_holder_pct": 15.3,
            "holders": [ {"address": "...", "amount": N, "percentage": P}, ... ]
        }
        """
        async with httpx.AsyncClient() as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [mint_address],
            }

            # Retry with rotation
            for _ in range(3):
                try:
                    current_rpc = self._get_rpc_url()
                    resp = await client.post(current_rpc, json=payload, timeout=10)
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except Exception:
                    continue
            else:
                 # Final attempt or fail
                 return None

            accounts = data.get("result", {}).get("value", [])
            if not accounts:
                return None

            # Get total supply to calculate percentages
            token_info = await self.get_token_info(mint_address)
            total_supply = token_info["supply"] if token_info else 0

            holders = []
            for acc in accounts[:top_n]:
                amount = int(acc.get("amount", 0))
                pct = (amount / total_supply * 100) if total_supply > 0 else 0
                holders.append({
                    "address": acc.get("address", ""),
                    "amount": amount,
                    "percentage": round(pct, 2),
                })

            top10_pct = sum(h["percentage"] for h in holders[:10])
            top20_pct = sum(h["percentage"] for h in holders[:20])

            return {
                "total_holders": None,  # RPC doesn't provide this, needs Helius
                "top10_pct": round(top10_pct, 2),
                "top20_pct": round(top20_pct, 2),
                "largest_holder_pct": holders[0]["percentage"] if holders else 0,
                "holders": holders,
            }



    async def check_lp_status(self, mint_address: str) -> dict:
        """
        Check Liquidity Pool status.

        NOTE: This is the most complex part since we need to check multiple DEXes
        (Raydium, Orca, Meteora). MVP only checks Raydium AMM pool.

        LP lock/burn check:
        1. Find pool account holding LP tokens
        2. Check LP token burn address (if sent to 1nc1...111 = burned)
        3. Or check lock contract

        Returns:
        {
            "has_liquidity": True,
            "lp_burned_pct": 95.0,
            "liquidity_locked": True,
            "dex": "raydium",
        }
        """
        # TODO: Implement Raydium pool check
        # For real implementation, use Helius DAS API or
        # Birdeye/DexScreener API for simplicity
        return {
            "has_liquidity": None,
            "lp_burned_pct": None,
            "liquidity_locked": None,
            "dex": None,
            "note": "LP check not yet implemented — needs DexScreener API integration",
        }
