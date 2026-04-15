"""
scrapers/rugcheck_client.py — RugCheck.xyz API wrapper

Public FREE endpoint (no API key). Returns insider/bundler/sniper percentages,
LP lock status, holder concentration, and a 1-10 risk score.

Integration contract:
  - `get_report(mint)` returns a normalised dict with `raw` preserved under a
    debug slot, OR `None` on failure. NEVER raises.
  - Graceful degradation: all scoring code must treat a None return as
    "data unavailable" and fall back to existing sources.
  - Caching uses the shared InMemoryCache so multiple audits of the same
    mint within TTL don't re-hit RugCheck.

ENV:
  RUGCHECK_BASE_URL   (default: https://api.rugcheck.xyz/v1)
  ENABLE_RUGCHECK     ("true"/"false", default true). When false, get_report
                      returns None without making a network call.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


_DEFAULT_BASE = "https://api.rugcheck.xyz/v1"
_CACHE_TTL = 300          # 5 min per spec
_TIMEOUT_SEC = 8.0
_MAX_RETRIES = 2
_BACKOFF_BASE = 0.8       # 0.8s, 1.6s — gentle exponential


class RugCheckClient:
    """Thin async wrapper around RugCheck.xyz's public token report endpoint."""

    def __init__(self, base_url: Optional[str] = None, cache=None):
        self.base_url = (base_url or os.getenv("RUGCHECK_BASE_URL") or _DEFAULT_BASE).rstrip("/")
        self.enabled = os.getenv("ENABLE_RUGCHECK", "true").lower() != "false"
        self._cache = cache  # optional utils.cache.InMemoryCache-compatible
        self._local_cache: dict[str, tuple[float, dict]] = {}

    async def get_report(self, mint: str) -> Optional[dict]:
        """
        Fetch and normalise a RugCheck token report.

        Returns a dict like:
          {
            "mint": str,
            "risk_score_1_10": int | None,
            "insider_pct": float,       # % supply held by flagged insiders
            "bundler_pct": float,       # % supply bundled at launch
            "sniper_pct": float,        # % supply sniped in first blocks
            "lp_locked_pct": float,     # 0-100, 100 means fully burned/locked
            "lp_burned": bool,
            "mint_authority_active": bool,
            "freeze_authority_active": bool,
            "top_holders_pct": float,   # top-10 combined %
            "risks": list[str],         # RugCheck human-readable labels
            "raw": dict,                # full API response, preserved
          }
        Returns None if disabled, network-failed after retries, or the
        response is missing entirely (empty body, 404, etc.).
        """
        if not self.enabled:
            return None
        mint = (mint or "").strip()
        if not mint:
            return None

        cache_key = f"rugcheck:{mint}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        raw = await self._fetch_with_retry(mint)
        if not raw:
            return None

        parsed = self._parse_report(mint, raw)
        if parsed is not None:
            await self._cache_set(cache_key, parsed)
        return parsed

    # ─────────────────────────────── internals ──────────────────────────────

    async def _fetch_with_retry(self, mint: str) -> Optional[dict]:
        url = f"{self.base_url}/tokens/{mint}/report"
        last_err: Exception | None = None

        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    resp = await client.get(url, headers={"accept": "application/json"})
                    if resp.status_code == 404:
                        logger.info("RugCheck 404 for %s (no report available)", mint)
                        return None
                    if resp.status_code == 429:
                        logger.warning("RugCheck 429 on %s (attempt %d)", mint, attempt + 1)
                    elif resp.status_code >= 500:
                        logger.warning("RugCheck %d on %s (attempt %d)", resp.status_code, mint, attempt + 1)
                    else:
                        resp.raise_for_status()
                        return resp.json() or None
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_err = e
                    logger.warning("RugCheck transport error on %s (attempt %d): %s", mint, attempt + 1, e)
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if 400 <= e.response.status_code < 500 and e.response.status_code not in (429,):
                        logger.info("RugCheck %d for %s — not retrying", e.response.status_code, mint)
                        return None
                except Exception as e:
                    last_err = e
                    logger.warning("RugCheck unexpected error on %s: %s", mint, e)

                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_BACKOFF_BASE * (2 ** attempt))

        if last_err:
            logger.info("RugCheck gave up on %s after retries: %s", mint, last_err)
        return None

    @staticmethod
    def _parse_report(mint: str, raw: dict) -> Optional[dict]:
        """
        Normalise the RugCheck response into the fields scoring code expects.

        RugCheck's schema varies slightly across versions; we read defensively
        and coerce types. Unknown fields fall back to sensible defaults that
        downstream scoring treats as "no signal".
        """
        if not isinstance(raw, dict):
            return None

        # --- Insider / bundler / sniper -----------------------------------
        insider_pct = _pct(raw.get("insiderPct") or raw.get("insider_pct"))
        bundler_pct = _pct(raw.get("bundlerPct") or raw.get("bundler_pct"))
        sniper_pct = _pct(raw.get("sniperPct") or raw.get("sniper_pct"))

        # --- LP lock status ----------------------------------------------
        # RugCheck returns `markets[].lp.lpLockedPct` plus a top-level hint.
        lp_locked_pct = _pct(
            raw.get("lpLockedPct")
            or raw.get("lp_locked_pct")
            or _walk_markets_lp(raw)
        )
        lp_burned = bool(raw.get("lpBurned") or raw.get("lp_burned") or False)
        if lp_burned and lp_locked_pct < 100:
            lp_locked_pct = 100.0

        # --- Authorities --------------------------------------------------
        mint_active = _truthy(raw.get("mintAuthority") or raw.get("mint_authority"))
        freeze_active = _truthy(raw.get("freezeAuthority") or raw.get("freeze_authority"))

        # --- Holder concentration ----------------------------------------
        top_holders = raw.get("topHolders") or raw.get("top_holders") or []
        top10_pct = _pct(raw.get("topHoldersPct") or raw.get("top_holders_pct"))
        if not top10_pct and isinstance(top_holders, list):
            top10_pct = sum(_pct(h.get("pct")) for h in top_holders[:10] if isinstance(h, dict))

        # --- Risks & 1-10 score ------------------------------------------
        risks_field = raw.get("risks") or []
        risks: list[str] = []
        for r in risks_field if isinstance(risks_field, list) else []:
            if isinstance(r, dict) and r.get("name"):
                risks.append(str(r["name"]))
            elif isinstance(r, str):
                risks.append(r)

        risk_score = raw.get("score") or raw.get("riskScore") or raw.get("risk_score")
        try:
            risk_score_1_10 = int(risk_score) if risk_score is not None else None
        except (TypeError, ValueError):
            risk_score_1_10 = None

        # --- Extra real-world fields observed in RugCheck responses -------
        rugged = bool(raw.get("rugged") or False)
        graph_insiders = raw.get("graphInsidersDetected") or 0
        try:
            graph_insiders = int(graph_insiders)
        except (TypeError, ValueError):
            graph_insiders = 0
        total_market_liquidity = raw.get("totalMarketLiquidity")
        detected_at = raw.get("detectedAt")

        return {
            "mint": mint,
            "risk_score_1_10": risk_score_1_10,
            "insider_pct": insider_pct,
            "bundler_pct": bundler_pct,
            "sniper_pct": sniper_pct,
            "lp_locked_pct": lp_locked_pct,
            "lp_burned": lp_burned,
            "mint_authority_active": mint_active,
            "freeze_authority_active": freeze_active,
            "top_holders_pct": top10_pct,
            "rugged": rugged,
            "graph_insiders_count": graph_insiders,
            "total_market_liquidity": total_market_liquidity,
            "detected_at": detected_at,
            "risks": risks,
            "raw": raw,
        }

    # ─────────────────────────── caching helpers ────────────────────────────

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


# ───────────────────────────── helpers ─────────────────────────────────────

def _pct(v) -> float:
    """Coerce arbitrary value to a 0-100 float. Returns 0.0 when unparsable."""
    try:
        if v is None:
            return 0.0
        n = float(v)
        if 0 <= n <= 1:
            # RugCheck sometimes sends fractions (0-1); convert to %
            return round(n * 100.0, 4)
        return round(n, 4)
    except (TypeError, ValueError):
        return 0.0


def _truthy(v) -> bool:
    """Return True when the authority field indicates an active authority."""
    if v in (None, "", 0, False, "null", "None"):
        return False
    if isinstance(v, str):
        return v.strip().lower() not in ("", "null", "none", "false", "0")
    return bool(v)


def _walk_markets_lp(raw: dict) -> float:
    """Pull an lpLockedPct from the first market entry, if present."""
    markets = raw.get("markets")
    if not isinstance(markets, list):
        return 0.0
    for m in markets:
        if not isinstance(m, dict):
            continue
        lp = m.get("lp") or {}
        pct = lp.get("lpLockedPct") or lp.get("lp_locked_pct")
        if pct is not None:
            return _pct(pct)
    return 0.0


# ─────────────────────────── module-level helper ───────────────────────────
# Shared singleton used by api/routes.py so we don't open a new client per call.

_singleton: RugCheckClient | None = None


def get_rugcheck_client(cache=None) -> RugCheckClient:
    global _singleton
    if _singleton is None:
        _singleton = RugCheckClient(cache=cache)
    return _singleton
