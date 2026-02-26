# scrapers/solana_scraper.py — Solana RPC / Helius API client

import httpx
from config import SOLANA_RPC_URL, HELIUS_API_KEY, ALCHEMY_RPC_URL, BIRDEYE_API_KEY
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SolanaScraper:
    """Fetch on-chain data for Solana tokens."""

    def __init__(self):
        self.rpc_urls = []
        
        # Prefer Alchemy if available (Custom User RPC) - PRIMARY
        if ALCHEMY_RPC_URL:
            self.rpc_urls.append(ALCHEMY_RPC_URL)
            
        # Prefer Helius if key is available - SECONDARY
        if HELIUS_API_KEY:
            self.rpc_urls.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
            self.helius_api = f"https://api.helius.xyz/v0"
        else:
            self.helius_api = None

        # Add public fallbacks ONLY if no private RPCs
        if not ALCHEMY_RPC_URL and not HELIUS_API_KEY:
            self.rpc_urls.extend([
                "https://api.mainnet-beta.solana.com",
                "https://rpc.ankr.com/solana",
                "https://solana-api.projectserum.com",
            ])
        
        self.current_rpc_index = 0

    def _normalize_address(self, addr: str) -> str:
        """Fix common Base58 typos (l -> 1, I -> L) and validate length.
        
        Solana addresses avoid 0, O, I, l characters.
        Only fixes clear typos: 'l' -> '1', 'I' -> 'L'
        """
        if not addr:
            return addr
        
        # Conservative fix: only 'l' and 'I' which are common typos
        # 'l' (lowercase L) looks like '1' in many fonts
        # 'I' (uppercase i) looks like 'L'
        fixed = addr.translate(str.maketrans('lI', '1L'))
        
        return fixed

    def _get_rpc_url(self):
        """Rotate RPC URL on failure."""
        url = self.rpc_urls[self.current_rpc_index]
        self.current_rpc_index = (self.current_rpc_index + 1) % len(self.rpc_urls)
        return url


    async def _fetch_dex_data(self, mint_address: str) -> Optional[dict]:
        """Fetch real-time market data from DexScreener (Solana only)."""
        async with httpx.AsyncClient() as client:
            # 1. Try as Token Address
            try:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_address}"
                resp = await client.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs") or []
                    # Filter for Solana pairs only
                    solana_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                    if solana_pairs:
                        best_pair = max(solana_pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
                        return best_pair
            except Exception as e:
                logger.warning(f"DexScreener token fetch failed: {e}")

            # 2. Try as Pair Address (Solana)
            try:
                url_pair = f"https://api.dexscreener.com/latest/dex/pairs/solana/{mint_address}"
                resp = await client.get(url_pair, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs") or []
                    if pairs and pairs[0].get("chainId") == "solana":
                        return pairs[0]
            except Exception as e:
                logger.warning(f"DexScreener pair fetch failed: {e}")

        return None

    async def get_token_symbol(self, mint_address: str) -> Optional[str]:
        """Resolve token symbol via DexScreener API."""
        pair = await self._fetch_dex_data(mint_address)
        if pair:
            return pair.get("baseToken", {}).get("symbol")
        return None

    async def get_holder_count(self, mint_address: str) -> Optional[int]:
        """Get total holder count via Birdeye API."""
        if not BIRDEYE_API_KEY:
            logger.warning("BIRDEYE_API_KEY not configured")
            return None
            
        from scrapers.birdeye_client import create_birdeye_client
        try:
            client = create_birdeye_client()
            overview = await client.get_token_overview(mint_address)
            if overview:
                holder_count = overview.get("holder")
                if holder_count is not None:
                    logger.info(f"Holder count for {mint_address}: {holder_count}")
                return holder_count
            return None
        except Exception as e:
            logger.warning(f"Birdeye API error in get_holder_count: {e}")
            return None
    

    async def get_token_info(self, mint_address: str) -> Optional[dict]:
        """
        Get basic token information from Solana + DexScreener.
        """
        # 1. Resolve actual mint address (in case user provided pair address or malformed string)
        # Use RAW address for DexScreener search (it handles fuzzy matching)
        dex_pair = await self._fetch_dex_data(mint_address)
        
        # DexScreener is surprisingly good at resolving malformed/wrong-case addresses.
        # But we must verify the result is a valid Solana Mint (43 or 44 chars).
        resolved_mint = dex_pair.get("baseToken", {}).get("address", mint_address) if dex_pair else mint_address
        
        # If DexScreener returned a valid-looking Solana address, use it.
        # Otherwise, fallback to the original input (might be a direct mint).
        if len(resolved_mint) in [43, 44]:
             actual_mint = resolved_mint
        else:
             actual_mint = mint_address

        # 2. Fetch Solana RPC data

        # 2. Fetch Solana RPC data
        async with httpx.AsyncClient() as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    actual_mint,
                    {"encoding": "jsonParsed"},
                ],
            }
            # Retry with rotation
            rpc_data = None
            for _ in range(3):
                try:
                    current_rpc = self._get_rpc_url()
                    resp = await client.post(current_rpc, json=payload, timeout=10)
                    resp.raise_for_status()
                    rpc_data = resp.json()
                    break
                except Exception:
                    continue

        if not rpc_data:
            return None

        account_data = rpc_data.get("result", {}).get("value", {})
        if not account_data:
            return None

        parsed = (
            account_data
            .get("data", {})
            .get("parsed", {})
            .get("info", {})
        )
        
        # Extract DexScreener data
        market_cap = 0
        pair_created_at = 0
        if dex_pair:
            market_cap = dex_pair.get("fdv", 0) or dex_pair.get("marketCap", 0)
            pair_created_at = dex_pair.get("pairCreatedAt", 0)

        return {
            "mint": actual_mint,
            "supply": int(parsed.get("supply", 0)),
            "decimals": parsed.get("decimals", 9),
            "symbol": dex_pair.get("baseToken", {}).get("symbol") if dex_pair else None,
            "marketCap": market_cap,
            "pairCreatedAt": pair_created_at,
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
        # Resolve actual mint address first
        token_info = await self.get_token_info(mint_address)
        if token_info and isinstance(token_info, dict):
            actual_mint = token_info.get("mint", self._normalize_address(mint_address))
            try:
                total_supply = int(token_info.get("supply") or 0)
            except Exception:
                total_supply = 0
        else:
            # Fallback: resolve mint from DexScreener even if RPC mint parsing failed
            actual_mint = self._normalize_address(mint_address)
            total_supply = 0
            try:
                dex_pair = await self._fetch_dex_data(mint_address)
                resolved_mint = dex_pair.get("baseToken", {}).get("address") if dex_pair else None
                if resolved_mint and len(resolved_mint) in [43, 44]:
                    actual_mint = resolved_mint
            except Exception:
                pass

        async with httpx.AsyncClient() as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [actual_mint],
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

            if not accounts:
                return None

            if total_supply <= 0:
                supply_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenSupply",
                    "params": [actual_mint],
                }
                for _ in range(3):
                    try:
                        current_rpc = self._get_rpc_url()
                        supply_resp = await client.post(current_rpc, json=supply_payload, timeout=10)
                        supply_resp.raise_for_status()
                        supply_data = supply_resp.json()
                        supply_val = (supply_data.get("result") or {}).get("value") or {}
                        supply_amount = supply_val.get("amount")
                        if supply_amount is not None:
                            total_supply = int(supply_amount)
                        break
                    except Exception:
                        continue

            holders = []
            for acc in accounts[:top_n]:
                amount = int(acc.get("amount", 0))
                pct = (amount / total_supply * 100) if total_supply > 0 else None
                holders.append({
                    "address": acc.get("address", ""),
                    "amount": amount,
                    "percentage": round(pct, 2) if pct is not None else None,
                })

            if total_supply > 0:
                top10_pct = sum(h["percentage"] for h in holders[:10] if isinstance(h.get("percentage"), (int, float)))
                top20_pct = sum(h["percentage"] for h in holders[:20] if isinstance(h.get("percentage"), (int, float)))
                largest_holder_pct = holders[0]["percentage"] if holders else 0
            else:
                top10_pct = None
                top20_pct = None
                largest_holder_pct = None

            return {
                "total_holders": None,
                "top10_pct": round(top10_pct, 2) if isinstance(top10_pct, (int, float)) else None,
                "top20_pct": round(top20_pct, 2) if isinstance(top20_pct, (int, float)) else None,
                "largest_holder_pct": largest_holder_pct,
                "holders": holders,
                "concentration_risk": "unknown" if total_supply <= 0 else "high" if (largest_holder_pct or 0) > 20 else "medium" if (top10_pct or 0) > 40 else "low",
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
