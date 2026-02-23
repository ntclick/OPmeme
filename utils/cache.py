# utils/cache.py — Multi-layer caching system

import json
import time
import hashlib
from typing import Optional, Any, Callable
from datetime import datetime, timedelta
from functools import wraps
import logging
import asyncio

logger = logging.getLogger(__name__)


class CacheEntry:
    """Single cache entry with metadata."""
    
    def __init__(self, value: Any, ttl_seconds: int):
        self.value = value
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl_seconds
        self.hits = 0
    
    def is_expired(self) -> bool:
        return time.time() > self.expires_at
    
    def time_remaining(self) -> int:
        return max(0, int(self.expires_at - time.time()))


class InMemoryCache:
    """Fast in-memory cache with TTL."""
    
    def __init__(self, max_size: int = 1000):
        self._cache: dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry and not entry.is_expired():
                entry.hits += 1
                logger.debug(f"Cache HIT: {key} (hits: {entry.hits}, ttl: {entry.time_remaining()}s)")
                return entry.value
            elif entry:
                # Expired, remove it
                del self._cache[key]
                logger.debug(f"Cache EXPIRED: {key}")
            return None
    
    async def set(self, key: str, value: Any, ttl: int = 300):
        """Set value with TTL (default 5 min)."""
        async with self._lock:
            # Evict oldest if full
            if len(self._cache) >= self._max_size:
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].created_at)
                del self._cache[oldest_key]
            
            self._cache[key] = CacheEntry(value, ttl)
            logger.info(f"Cache SET: {key} (TTL: {ttl}s)")
    
    async def delete(self, key: str):
        """Delete specific key."""
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
    
    async def clear(self):
        """Clear all cache."""
        async with self._lock:
            self._cache.clear()
    
    def stats(self) -> dict:
        """Get cache statistics."""
        total_hits = sum(e.hits for e in self._cache.values())
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "total_hits": total_hits,
            "keys": list(self._cache.keys())
        }


# Global cache instance
cache = InMemoryCache(max_size=500)


# ─── Cache Keys ────────────────────────────────────────────────────────────────

class CacheKeys:
    """Standardized cache key generators."""
    
    @staticmethod
    def token_info(mint: str) -> str:
        return f"token_info:{mint}"
    
    @staticmethod
    def dex_data(mint: str) -> str:
        return f"dex:{mint}"
    
    @staticmethod
    def holder_data(mint: str) -> str:
        return f"holders:{mint}"
    
    @staticmethod
    def price_history(mint: str, interval: str) -> str:
        return f"price:{mint}:{interval}"
    
    @staticmethod
    def trade_data(mint: str) -> str:
        return f"trades:{mint}"
    
    @staticmethod
    def jupiter_quote(mint: str, amount: float) -> str:
        return f"jup_quote:{mint}:{amount}"
    
    @staticmethod
    def tweets(ticker: str) -> str:
        # Normalize ticker: remove $/# prefix, uppercase, strip whitespace
        normalized = ticker.upper().lstrip("$#").strip()
        return f"tweets:{normalized}"
    
    @staticmethod
    def full_analysis(identifier: str) -> str:
        # If identifier looks like a mint address (base58, 32-44 chars), use as-is
        # Otherwise normalize as ticker (uppercase, strip $/#)
        if len(identifier) > 30 and identifier.isalnum():
            # Likely a Solana mint address
            return f"analysis:{identifier}"
        # Normalize as ticker
        normalized = identifier.upper().lstrip("$#").strip()
        return f"analysis:{normalized}"
    
    @staticmethod
    def ai_verdict(ticker: str) -> str:
        # Normalize ticker: remove $/# prefix, uppercase, strip whitespace
        normalized = ticker.upper().lstrip("$#").strip()
        return f"ai_verdict:{normalized}"


# ─── Cache TTL Config ──────────────────────────────────────────────────────────

class CacheTTL:
    """TTL configuration in seconds."""
    
    # Fast-changing data
    PRICE = 30              # 30 seconds
    DEX_DATA = 60           # 1 minute
    JUPITER_QUOTE = 60      # 1 minute
    
    # Medium-changing data
    HOLDER_DATA = 120       # 2 minutes
    TRADE_DATA = 120        # 2 minutes
    PRICE_HISTORY = 300     # 5 minutes
    
    # Slow-changing data
    TOKEN_INFO = 600        # 10 minutes
    TWEETS = 900            # 15 minutes
    
    # Analysis results
    FULL_ANALYSIS = 900     # 15 minutes
    AI_VERDICT = 1800       # 30 minutes


# ─── Decorator for automatic caching ───────────────────────────────────────────

def cached(key_func: Callable, ttl: int = 300):
    """
    Decorator to automatically cache function results.
    
    Usage:
        @cached(lambda mint: f"token:{mint}", ttl=60)
        async def get_token_info(mint: str) -> dict:
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = key_func(*args, **kwargs)
            
            # Try cache first
            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Call actual function
            result = await func(*args, **kwargs)
            
            # Cache result if not None
            if result is not None:
                await cache.set(cache_key, result, ttl)
            
            return result
        
        return wrapper
    return decorator


# ─── Backward Compatibility ─────────────────────────────────────────────────────

# Legacy cache store (for backward compatibility)
_cache: dict[str, dict] = {}


def check_cache(key: str, ttl_minutes: int) -> Optional[dict]:
    """Legacy function - redirects to new cache system."""
    entry = _cache.get(key)
    if not entry:
        return None
    expires_at = entry.get("expires_at")
    if expires_at and datetime.utcnow() > expires_at:
        del _cache[key]
        return None
    return entry.get("data")


def set_cache(key: str, data: Any, ttl_minutes: int) -> None:
    """Legacy function - redirects to new cache system."""
    serialized = json.loads(json.dumps(data, default=str))
    _cache[key] = {
        "data": serialized,
        "expires_at": datetime.utcnow() + timedelta(minutes=ttl_minutes),
        "created_at": datetime.utcnow(),
    }
    logger.info(f"Cache SET for key: {key} (TTL: {ttl_minutes}m)")


def clear_cache() -> int:
    """Clear all cache entries."""
    count = len(_cache)
    _cache.clear()
    return count


def cleanup_expired() -> int:
    """Remove expired entries."""
    now = datetime.utcnow()
    expired_keys = [
        k for k, v in _cache.items()
        if v.get("expires_at") and now > v["expires_at"]
    ]
    for k in expired_keys:
        del _cache[k]
    return len(expired_keys)
