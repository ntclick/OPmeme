# utils/http_client.py — HTTP client with retry, timeout, and fallback

import httpx
import asyncio
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ResilientClient:
    """HTTP client with retry, timeout, and fallback."""
    
    def __init__(self):
        self.timeout = httpx.Timeout(10.0, connect=5.0)
    
    async def get(
        self,
        url: str,
        headers: dict = None,
        params: dict = None,
        retries: int = 3,
        backoff: float = 1.0
    ) -> Optional[dict]:
        """GET request with retry logic."""
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(retries):
                try:
                    resp = await client.get(url, headers=headers, params=params)
                    resp.raise_for_status()
                    return resp.json()
                    
                except httpx.ConnectError as e:
                    logger.warning(f"Connection error (attempt {attempt+1}/{retries}): {e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(backoff * (2 ** attempt))
                    continue
                    
                except httpx.TimeoutException:
                    logger.warning(f"Timeout (attempt {attempt+1}/{retries})")
                    if attempt < retries - 1:
                        await asyncio.sleep(backoff)
                    continue
                    
                except httpx.HTTPStatusError as e:
                    logger.warning(f"HTTP {e.response.status_code}: {url}")
                    return None
                    
                except Exception as e:
                    logger.error(f"Unexpected error: {e}")
                    return None
        
        return None
    
    async def post(
        self,
        url: str,
        json: dict = None,
        headers: dict = None,
        retries: int = 3
    ) -> Optional[dict]:
        """POST request with retry logic."""
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(retries):
                try:
                    resp = await client.post(url, json=json, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
                except Exception as e:
                    logger.warning(f"POST failed (attempt {attempt+1}/{retries}): {e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(1 * (2 ** attempt))
                    continue
        
        return None


# Global instance
http_client = ResilientClient()
