"""
scrapers/solana_tracker_client.py — Solana Tracker API wrapper.

Third-party source used ONLY for cross-validation of risk signals. Scoring
does not depend on it — it's a confidence booster.

Docs: https://data.solanatracker.io  (free tier requires API key signup)

ENV (all optional):
  SOLANA_TRACKER_API_KEY   — when missing/empty, client returns None for
                             every call without making any HTTP request.
  ENABLE_SOLANA_TRACKER    — set to "false" to force-disable even with a key.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://data.solanatracker.io"
_CACHE_TTL = 300           # match RugCheck 5-min cache
_TIMEOUT_SEC = 8.0
_MAX_RETRIES = 1


class SolanaTrackerClient:
    """Thin async wrapper around Solana Tracker's token endpoint."""

    def __init__(self, api_key: Optional[str] = None, cache=None):
        self.api_key = api_key or os.getenv("SOLANA_TRACKER_API_KEY") or ""
        self.enabled = (
            bool(self.api_key.strip())
            and os.getenv("ENABLE_SOLANA_TRACKER", "true").lower() != "false"
        )
        self._cache = cache
        self._local_cache: dict[str, tuple[float, dict]] = {}

    async def get_token(self, mint: str) -> Optional[dict]:
        """
        Fetch and normalise a Solana Tracker token report.

        Returns a dict with keys the cross_validator understands
        (risk_score, top10_pct, …) OR None on disable/failure.
        """
        if not self.enabled:
            return None
        mint = (mint or "").strip()
        if not mint:
            return None

        cache_key = f"solana_tracker:{mint}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        raw = await self._fetch_with_retry(mint)
        if not raw:
            return None
        parsed = self._parse(mint, raw)
        if parsed is not None:
            await self._cache_set(cache_key, parsed)
        return parsed

    async def _fetch_with_retry(self, mint: str) -> Optional[dict]:
        url = f"{_BASE_URL}/tokens/{mint}"
        headers = {"x-api-key": self.api_key, "accept": "application/json"}

        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 404:
                        return None
                    if resp.status_code in (401, 403):
                        logger.warning("Solana Tracker auth failed (%d) — check API key", resp.status_code)
                        return None
                    if resp.status_code >= 500 or resp.status_code == 429:
                        logger.info("Solana Tracker %d (attempt %d)", resp.status_code, attempt + 1)
                    else:
                        resp.raise_for_status()
                        return resp.json() or None
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    logger.info("Solana Tracker transport error (attempt %d): %s", attempt + 1, e)
                except Exception as e:
                    logger.info("Solana Tracker error: %s", e)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(0.8 * (attempt + 1))
        return None

    @staticmethod
    def _parse(mint: str, raw: dict) -> Optional[dict]:
        """Extract fields the cross_validator consumes. Defensive."""
        if not isinstance(raw, dict):
            return None

        # Solana Tracker's `risk` object holds aggregated signals; `score` is
        # usually 1-10. Structure varies by account tier — read defensively.
        risk_obj = raw.get("risk") or {}
        score = risk_obj.get("score") if isinstance(risk_obj, dict) else None
        if score is None:
            score = raw.get("riskScore") or raw.get("risk_score")

        top_holders = raw.get("topHolders") or raw.get("top_holders") or []
        top10_pct = 0.0
        if isinstance(top_holders, list):
            for h in top_holders[:10]:
                if not isinstance(h, dict):
                    continue
                pct = h.get("pct") or h.get("percentage") or 0
                try:
                    pct = float(pct)
                    top10_pct += pct * 100 if pct <= 1 else pct
                except (TypeError, ValueError):
                    continue

        try:
            risk_score = int(score) if score is not None else None
        except (TypeError, ValueError):
            risk_score = None

        return {
            "mint": mint,
            "risk_score": risk_score,      # 1-10 scale
            "top10_pct": round(top10_pct, 4) if top10_pct else None,
            "raw": raw,
        }

    # ── caching (mirrors RugCheckClient helper shape) ──────────────────────

    async def _cache_get(self, key: str):
        if self._cache is not None:
            try:
                return await self._cache.get(key)
            except Exception:
                pass
        import time
        hit = self._local_cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        return None

    async def _cache_set(self, key: str, value: dict):
        if self._cache is not None:
            try:
                await self._cache.set(key, value, ttl=_CACHE_TTL)
                return
            except Exception:
                pass
        import time
        self._local_cache[key] = (time.time() + _CACHE_TTL, value)


_singleton: Optional[SolanaTrackerClient] = None


def get_solana_tracker_client(cache=None) -> SolanaTrackerClient:
    global _singleton
    if _singleton is None:
        _singleton = SolanaTrackerClient(cache=cache)
    return _singleton
