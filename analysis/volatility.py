"""
analysis/volatility.py — Realized volatility from OHLCV candles.

Feeds off Birdeye `/defi/ohlcv?type=1H` (24 hourly candles). We compute
log-returns between consecutive close prices and annualise by
sqrt(candles_per_year). Default is 24 candles/day × 365 days.

Pure function, no I/O.
"""
from __future__ import annotations

import math
import statistics
from typing import Iterable, Optional

# Expected candle shape (Birdeye): {"unixTime": int, "o": float, "h": float,
# "l": float, "c": float, "v": float}. Accepts snake/camel variants defensively.


def _close_price(candle: dict) -> Optional[float]:
    if not isinstance(candle, dict):
        return None
    for key in ("c", "close", "Close"):
        v = candle.get(key)
        if v is None:
            continue
        try:
            f = float(v)
            if f > 0:
                return f
        except (TypeError, ValueError):
            continue
    return None


def realized_volatility(
    candles: Iterable[dict],
    candles_per_year: float = 24 * 365,
    min_candles: int = 6,
) -> Optional[float]:
    """
    Annualized realized volatility.

    Args:
        candles: iterable of OHLCV dicts (close price extracted from `c`)
        candles_per_year: scaling factor. Default 24×365 for 1h candles.
                          Use 365 for daily, 24*365*60 for 1-minute, etc.
        min_candles: below this many valid closes → return None.

    Returns:
        Annualized stddev of log-returns, or None when data is insufficient
        (fewer than min_candles, constant price, or parse errors).
    """
    closes = [c for c in (_close_price(x) for x in candles or []) if c is not None]
    if len(closes) < min_candles:
        return None

    log_returns: list[float] = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if prev <= 0 or cur <= 0:
            continue
        try:
            log_returns.append(math.log(cur / prev))
        except (ValueError, ZeroDivisionError):
            continue

    # stdev requires at least 2 data points
    if len(log_returns) < 2:
        return None
    sigma = statistics.pstdev(log_returns)  # population stddev — stable on small samples
    if sigma == 0:
        return 0.0
    return sigma * math.sqrt(candles_per_year)


def volume_trend_delta(candles: Iterable[dict]) -> Optional[float]:
    """
    Percent change in volume comparing the latest half of the window
    against the earlier half. Positive = volume ramping up.

    Returns None when data is insufficient.
    """
    vols: list[float] = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        for key in ("v", "volume", "Volume"):
            v = c.get(key)
            if v is None:
                continue
            try:
                vols.append(float(v))
                break
            except (TypeError, ValueError):
                continue
    if len(vols) < 4:
        return None
    half = len(vols) // 2
    earlier = sum(vols[:half])
    later = sum(vols[half:])
    if earlier <= 0:
        return None
    return round(((later - earlier) / earlier) * 100.0, 2)
