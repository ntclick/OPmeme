# scrapers/birdeye_client.py — Birdeye API Client v3.1

import httpx
from typing import Optional, Dict, Any
import logging
import asyncio
import os

logger = logging.getLogger(__name__)


class BirdeyeClient:
    """
    Birdeye API Client for Solana Token Analysis.
    
    IMPORTANT: Requires x-chain header for all requests!
    
    Tiers:
    - Standard (FREE): token_overview, price, holder, trending
    - Lite ($49/mo): + token_security, creation_info
    - Starter ($99/mo): + detailed holder list
    - Premium ($299/mo): + WebSocket
    """
    
    BASE_URL = "https://public-api.birdeye.so"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "X-API-KEY": api_key,
            "x-chain": "solana",  # ⚠️ CRITICAL - without this = 400 error!
            "accept": "application/json"
        }
        
        if not api_key:
            logger.warning("⚠️ BIRDEYE_API_KEY not set - API calls will fail!")
    
    async def _request(
        self,
        endpoint: str,
        params: dict = None,
        timeout: float = 15.0
    ) -> Optional[Dict[str, Any]]:
        """
        Make API request with comprehensive error handling.
        
        Returns:
            dict: Response data if successful
            None: On any error
        """
        url = f"{self.BASE_URL}{endpoint}"
        
        async with httpx.AsyncClient() as client:
            try:
                logger.debug(f"🐦 Birdeye request: {endpoint} params={params}")
                
                resp = await client.get(
                    url,
                    params=params,
                    headers=self.headers,
                    timeout=timeout
                )
                
                # Handle specific error codes
                if resp.status_code == 400:
                    logger.error(f"❌ Birdeye 400 Bad Request: {resp.text}")
                    logger.error("   → Check if x-chain header is set correctly")
                    return None
                
                if resp.status_code == 403:
                    logger.error(f"❌ Birdeye 403 Forbidden: API key invalid or endpoint not in plan")
                    return None
                
                if resp.status_code == 429:
                    logger.warning("⚠️ Birdeye 429 Rate Limited - backing off")
                    await asyncio.sleep(2)
                    return None
                
                if resp.status_code == 404:
                    logger.warning(f"⚠️ Birdeye 404: Token not found")
                    return None
                
                resp.raise_for_status()
                data = resp.json()
                
                # Check Birdeye success flag
                if not data.get("success"):
                    logger.warning(f"⚠️ Birdeye returned success=false: {data}")
                    return None
                
                return data.get("data")
                
            except httpx.TimeoutException:
                logger.warning(f"⚠️ Birdeye timeout on {endpoint}")
                return None
            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Birdeye HTTP error: {e}")
                return None
            except Exception as e:
                logger.error(f"❌ Birdeye request failed: {e}")
                return None
    
    # ════════════════════════════════════════════════════════════════════════
    # STANDARD TIER ENDPOINTS (FREE)
    # ════════════════════════════════════════════════════════════════════════
    
    async def get_token_overview(self, mint: str) -> Optional[dict]:
        """
        Get comprehensive token overview - BEST SINGLE ENDPOINT!
        
        Returns: price, liquidity, mc, holder count, volume, trades, etc.
        
        This is the GOLDMINE endpoint - one call gets most of what we need.
        """
        # Rate limit protection - Birdeye free tier is ~10 req/s
        await asyncio.sleep(0.2)
        
        data = await self._request("/defi/token_overview", {"address": mint})
        
        if data:
            logger.info(
                f"✅ Birdeye overview: holder={data.get('holder')}, "
                f"vol24h=${data.get('v24hUSD', 0):,.0f}, "
                f"mc=${data.get('mc', 0):,.0f}"
            )
        
        return data
    
    async def get_price(self, mint: str, include_liquidity: bool = True) -> Optional[dict]:
        """Get current token price."""
        return await self._request("/defi/price", {
            "address": mint,
            "include_liquidity": str(include_liquidity).lower()
        })
    
    async def get_token_holders(
        self,
        mint: str,
        limit: int = 100,
        offset: int = 0
    ) -> Optional[dict]:
        """Get top token holders list."""
        return await self._request("/defi/v3/token/holder", {
            "address": mint,
            "limit": min(limit, 100),
            "offset": offset
        })
    
    async def get_market_data(self, mint: str) -> Optional[dict]:
        """Get real-time market data (price, liquidity, mc)."""
        return await self._request("/defi/v3/token/market-data", {"address": mint})
    
    async def get_trending(self, sort_by: str = "rank", limit: int = 20) -> Optional[list]:
        """Get trending tokens."""
        data = await self._request("/defi/token_trending", {
            "sort_by": sort_by,
            "sort_type": "asc",
            "limit": min(limit, 20)
        })
        return data.get("tokens") if data else None
    
    async def search(self, keyword: str, limit: int = 10) -> Optional[list]:
        """Search for tokens."""
        data = await self._request("/defi/v3/search", {
            "keyword": keyword,
            "chain": "solana",
            "limit": limit
        })
        return data.get("items") if data else None
    
    async def get_ohlcv(
        self,
        mint: str,
        interval: str = "15m",
        limit: int = 100
    ) -> Optional[list]:
        """
        Get OHLCV candlestick data.
        
        Intervals: 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 8H, 12H, 1D, 3D, 1W, 1M
        """
        import time
        
        interval_seconds = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
            "8H": 28800, "12H": 43200, "1D": 86400
        }
        seconds = interval_seconds.get(interval, 900)
        
        time_to = int(time.time())
        time_from = time_to - (seconds * limit)
        
        data = await self._request("/defi/ohlcv", {
            "address": mint,
            "type": interval,
            "time_from": time_from,
            "time_to": time_to
        })
        return data.get("items") if data else None
    
    async def get_smart_money_stats(self, mint: str) -> Optional[dict]:
        """Get smart money stats for token (may require higher tier)."""
        return await self._request("/smart_money/token_stats", {"address": mint})

    # ════════════════════════════════════════════════════════════════════════
    # LITE+ TIER ENDPOINTS (Requires paid plan)
    # ════════════════════════════════════════════════════════════════════════
    
    async def get_token_security(self, mint: str) -> Optional[dict]:
        """
        Get token security info (Lite+ required).
        
        Returns: mint/freeze authority, top holder %, creator info.
        """
        return await self._request("/defi/token_security", {"address": mint})
    
    async def get_token_creation(self, mint: str) -> Optional[dict]:
        """Get token creation info (Lite+ required)."""
        return await self._request("/defi/token_creation_info", {"address": mint})
    
    # ════════════════════════════════════════════════════════════════════════
    # CONVENIENCE METHODS
    # ════════════════════════════════════════════════════════════════════════
    
    async def get_full_analysis(self, mint: str) -> Dict[str, Any]:
        """
        Get all available data for comprehensive analysis.
        
        Makes parallel API calls. Watch rate limits on free tier!
        """
        tasks = [
            self.get_token_overview(mint),
            self.get_token_holders(mint, limit=20),
            self.get_token_security(mint),  # May fail on free tier
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        overview = results[0] if not isinstance(results[0], Exception) else None
        holders = results[1] if not isinstance(results[1], Exception) else None
        security = results[2] if not isinstance(results[2], Exception) else None
        
        return {
            "overview": overview,
            "holders": holders,
            "security": security,
            "success": overview is not None
        }
    
    def extract_scores_from_overview(self, overview: dict) -> dict:
        """
        Extract key metrics for scoring from token_overview response.
        
        This is the main method used by routes.py to get structured data.
        
        Birdeye API field names (from docs):
        - holder: number of holders
        - marketCap / mc: market cap (both may exist)
        - liquidity: liquidity in USD
        - v24hUSD: volume 24h in USD
        - priceChange1hPercent: 1h price change %
        - priceChange24hPercent: 24h price change %
        """
        if not overview:
            return {}
        
        # Safe extraction with defaults - handle both field name variants
        volume_24h = overview.get("v24hUSD") or overview.get("v24h") or 0
        buy_volume = overview.get("vBuy24hUSD") or overview.get("vBuy24h") or 0
        sell_volume = overview.get("vSell24hUSD") or overview.get("vSell24h") or 0
        buys = overview.get("buy24h") or 0
        sells = overview.get("sell24h") or 1  # Avoid division by zero
        
        # Market cap - check both field names
        market_cap = overview.get("marketCap") or overview.get("mc") or 0
        
        # Liquidity - check both field names
        liquidity = overview.get("liquidity") or overview.get("liquidityUsd") or 0
        
        return {
            # Market Data
            "price": overview.get("price"),
            "liquidity": liquidity,
            "market_cap": market_cap,
            
            # Holders (UNIQUE to Birdeye!)
            "holder_count": overview.get("holder"),
            "unique_traders_24h": overview.get("uniqueWallet24h"),
            
            # Volume
            "volume_24h": volume_24h,
            "buy_volume_24h": buy_volume,
            "sell_volume_24h": sell_volume,
            
            # Trades
            "trades_24h": overview.get("trade24h"),
            "buys_24h": buys,
            "sells_24h": sells,
            
            # Price Changes
            "price_change_1h": overview.get("priceChange1hPercent"),
            "price_change_24h": overview.get("priceChange24hPercent"),
            
            # Calculated Ratios
            "buy_sell_ratio": buys / sells if sells > 0 else 1.0,
            "buy_volume_ratio": buy_volume / volume_24h if volume_24h > 0 else 0.5,
        }


# ════════════════════════════════════════════════════════════════════════════
# Factory Function
# ════════════════════════════════════════════════════════════════════════════

def create_birdeye_client(api_key: str = None) -> BirdeyeClient:
    """
    Create Birdeye client with API key from env if not provided.
    
    Set BIRDEYE_API_KEY environment variable.
    """
    key = api_key or os.getenv("BIRDEYE_API_KEY", "")
    
    if not key:
        logger.warning("⚠️ BIRDEYE_API_KEY not set - Birdeye calls will fail!")
        logger.warning("   → Get free API key at https://birdeye.so/")
    
    return BirdeyeClient(key)
