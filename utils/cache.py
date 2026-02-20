# utils/cache.py — Simple in-memory cache with TTL

from datetime import datetime, timedelta
from typing import Optional, Any
import json
import logging

logger = logging.getLogger(__name__)

# In-memory cache store.  Replace with Redis when scaling.
_cache: dict[str, dict] = {}


def check_cache(key: str, ttl_minutes: int) -> Optional[dict]:
    """
    Check if a cached value exists and is still valid.

    Args:
        key: Cache key (e.g. "analysis:PEPE")
        ttl_minutes: Max age in minutes

    Returns:
        Cached dict if valid, None if expired or missing.
    """
    entry = _cache.get(key)
    if not entry:
        return None

    expires_at = entry.get("expires_at")
    if expires_at and datetime.utcnow() > expires_at:
        # Expired — remove it
        del _cache[key]
        return None

    logger.info(f"Cache HIT for key: {key}")
    return entry.get("data")


def set_cache(key: str, data: Any, ttl_minutes: int) -> None:
    """
    Store a value in cache with TTL.

    Args:
        key: Cache key
        data: Data to cache (must be JSON-serializable for the response)
        ttl_minutes: Minutes until expiry
    """
    # Deep-copy via JSON round-trip to avoid reference issues
    # (and to ensure datetime serialization works)
    serialized = json.loads(
        json.dumps(data, default=str)
    )

    _cache[key] = {
        "data": serialized,
        "expires_at": datetime.utcnow() + timedelta(minutes=ttl_minutes),
        "created_at": datetime.utcnow(),
    }
    logger.info(f"Cache SET for key: {key} (TTL: {ttl_minutes}m)")


def clear_cache() -> int:
    """Clear all cache entries. Returns number of entries cleared."""
    count = len(_cache)
    _cache.clear()
    return count


def cleanup_expired() -> int:
    """Remove expired entries. Returns number of entries removed."""
    now = datetime.utcnow()
    expired_keys = [
        k for k, v in _cache.items()
        if v.get("expires_at") and now > v["expires_at"]
    ]
    for k in expired_keys:
        del _cache[k]
    return len(expired_keys)
