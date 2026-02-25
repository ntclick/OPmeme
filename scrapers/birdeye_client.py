# scrapers/birdeye_client.py — Birdeye API Client v3.1

import httpx
from typing import Optional, Dict, Any
import logging
import asyncio
import os
import time

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

        self._token_overview_cache: Dict[str, Any] = {}
        self._token_overview_cache_expiry: Dict[str, float] = {}
        
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
                    reset = resp.headers.get("x-ratelimit-reset")
                    sleep_seconds = 2.0
                    try:
                        if reset:
                            reset_ts = int(reset)
                            now_ts = int(time.time())
                            if reset_ts > now_ts:
                                sleep_seconds = min(max(reset_ts - now_ts, 1), 30)
                    except Exception:
                        pass
                    logger.warning(f"⚠️ Birdeye 429 Rate Limited - backing off {sleep_seconds}s")
                    await asyncio.sleep(sleep_seconds)
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
        cached = self._token_overview_cache.get(mint)
        expiry = self._token_overview_cache_expiry.get(mint)
        if cached and expiry and time.time() < expiry:
            return cached

        await asyncio.sleep(0.2)
        
        data = await self._request("/defi/token_overview", {"address": mint})
        
        if data:
            logger.info(
                f"✅ Birdeye overview: holder={data.get('holder')}, "
                f"vol24h=${(data.get('v24hUSD') or data.get('v1440mUSD') or 0):,.0f}, "
                f"mc=${(data.get('mc') or data.get('marketCap') or 0):,.0f}"
            )

            self._token_overview_cache[mint] = data
            self._token_overview_cache_expiry[mint] = time.time() + 5
        
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

        def _first(*keys: str, default=None):
            for k in keys:
                v = overview.get(k)
                if v is not None:
                    return v
            return default

        def _num(v, default: float = 0.0) -> float:
            if v is None:
                return default
            try:
                return float(v)
            except Exception:
                return default

        def _int(v, default: int = 0) -> int:
            if v is None:
                return default
            try:
                return int(v)
            except Exception:
                return default

        # Safe extraction with defaults - support both legacy 24h fields and newer 1440m fields
        volume_24h = _num(_first("v24hUSD", "v24h", "v1440mUSD", "v1440m", default=0))
        buy_volume = _num(_first("vBuy24hUSD", "vBuy24h", "vBuy1440mUSD", "vBuy1440m", default=0))
        sell_volume = _num(_first("vSell24hUSD", "vSell24h", "vSell1440mUSD", "vSell1440m", default=0))

        buys = _int(_first("buy24h", "buy1440m", default=0))
        sells = _int(_first("sell24h", "sell1440m", default=0))
        sells_for_ratio = sells if sells > 0 else 1

        trades_24h = _int(_first("trade24h", "trade1440m", default=0))
        unique_traders_24h = _int(_first("uniqueWallet24h", "uniqueWallet1440m", default=0))

        # Market cap - check multiple field names
        market_cap = _num(_first("marketCap", "mc", "fdv", default=0))

        # Liquidity - check multiple field names
        liquidity = _num(_first("liquidity", "liquidityUsd", "liquidityUSD", default=0))

        price_change_1h = _num(_first("priceChange1hPercent", "priceChange60mPercent", default=0))
        price_change_24h = _num(_first("priceChange24hPercent", "priceChange1440mPercent", default=0))

        volume_24h_change = _num(
            _first(
                "v24hChangePercent",
                "v24hUSDChangePercent",
                "v1440mChangePercent",
                "v1440mUSDChangePercent",
                default=0,
            )
        )
        liquidity_change_24h = _num(
            _first(
                "liquidityChange24hPercent",
                "liquidityChange1440mPercent",
                "liquidityChangePercent",
                default=0,
            )
        )

        return {
            # Market Data
            "price": _num(overview.get("price"), 0.0) or None,
            "liquidity": liquidity,
            "market_cap": market_cap,
            
            # Holders (UNIQUE to Birdeye!)
            "holder_count": _int(_first("holder", "holders", default=0)) or None,
            "unique_traders_24h": unique_traders_24h,
            
            # Volume
            "volume_24h": volume_24h,
            "buy_volume_24h": buy_volume,
            "sell_volume_24h": sell_volume,
            "volume_24h_change": volume_24h_change,
            
            # Trades
            "trades_24h": trades_24h,
            "buys_24h": buys,
            "sells_24h": sells,
            
            # Price Changes
            "price_change_1h": price_change_1h,
            "price_change_24h": price_change_24h,

            # Liquidity changes
            "liquidity_change_24h": liquidity_change_24h,
            
            # Calculated Ratios
            "buy_sell_ratio": buys / sells_for_ratio,
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
