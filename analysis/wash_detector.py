"""
analysis/wash_detector.py — Wash trade heuristic scorer.

Consumes a list of recent transactions (shape mirrors Birdeye
`/defi/txs/token` response) and returns a 0..1 score where:

  0.0 = healthy organic activity
  1.0 = full wash / fake volume

Three sub-signals (weights loaded from scoring_thresholds.yaml):

  1. same_wallet_rapid_flip  — wallets that buy and sell within a short
     window (default 60s). Classic wash signature.
  2. low_unique_ratio        — unique_traders / total_txs below a floor
     (default 0.30). Fewer distinct wallets than real trading produces.
  3. volume_concentration    — one or two wallets moving > X% (default 50%)
     of the total volume across the window.

All helpers are pure functions — no I/O. Returns `insufficient_data` flag
when fewer than `min_txs_required` txs are provided so the caller can
decide whether to penalise, neutralise or skip the score.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Sequence

import scoring_loader


def _get_cfg() -> dict:
    """Read wash_detection section once per call — hot-reloadable via YAML."""
    return scoring_loader.get_section("wash_detection") or {}


def _normalise_tx(tx: dict) -> dict | None:
    """
    Flatten a Birdeye tx record into the internal shape we use.

    Birdeye `/defi/txs/token` returns records with fields that vary by
    version (side, owner, volumeUSD, blockUnixTime, etc.). We pull the
    ones we need defensively — unknown shapes return None.
    """
    if not isinstance(tx, dict):
        return None
    side = (tx.get("side") or tx.get("txType") or "").lower()
    if side not in ("buy", "sell"):
        return None
    wallet = tx.get("owner") or tx.get("wallet") or tx.get("from") or tx.get("user")
    if not wallet:
        return None
    try:
        ts = int(tx.get("blockUnixTime") or tx.get("timestamp") or tx.get("time") or 0)
    except (TypeError, ValueError):
        ts = 0
    try:
        volume = float(tx.get("volumeUSD") or tx.get("volume_usd") or tx.get("volumeUsd") or tx.get("value_usd") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    return {"wallet": str(wallet), "side": side, "ts": ts, "volume_usd": volume}


def _rapid_flip_ratio(txs: list[dict], window_sec: int) -> float:
    """
    Fraction of txs that are part of a rapid buy-then-sell (or sell-then-buy)
    by the same wallet within `window_sec`. Returns 0..1.
    """
    if not txs:
        return 0.0
    by_wallet: dict[str, list[dict]] = defaultdict(list)
    for t in txs:
        by_wallet[t["wallet"]].append(t)

    flagged = 0
    for events in by_wallet.values():
        if len(events) < 2:
            continue
        events.sort(key=lambda e: e["ts"])
        # Walk adjacent events; flag both if opposite side and within window.
        for i in range(len(events) - 1):
            a, b = events[i], events[i + 1]
            if a["side"] == b["side"]:
                continue
            if a["ts"] == 0 or b["ts"] == 0:
                continue
            if (b["ts"] - a["ts"]) <= window_sec:
                flagged += 2  # count both sides of the flip
    return min(1.0, flagged / max(1, len(txs)))


def _unique_ratio_penalty(txs: list[dict], threshold: float) -> float:
    """
    Returns 0..1 where higher means worse uniqueness. Linear ramp from 0
    at `threshold` up to 1 when unique_ratio approaches 0.
    """
    if not txs:
        return 0.0
    unique = len({t["wallet"] for t in txs})
    ratio = unique / len(txs)
    if ratio >= threshold:
        return 0.0
    # Normalise: below threshold scales linearly to 1 at ratio=0
    return min(1.0, (threshold - ratio) / threshold)


def _concentration_penalty(txs: list[dict], threshold: float) -> float:
    """
    Returns 0..1 based on share of total volume held by the top 2 wallets.
    At or below threshold → 0. Above threshold → ramps linearly to 1 at 100%.
    """
    if not txs:
        return 0.0
    vol_by_wallet: dict[str, float] = defaultdict(float)
    total = 0.0
    for t in txs:
        vol_by_wallet[t["wallet"]] += t["volume_usd"]
        total += t["volume_usd"]
    if total <= 0:
        return 0.0
    top_two = sorted(vol_by_wallet.values(), reverse=True)[:2]
    share = sum(top_two) / total
    if share <= threshold:
        return 0.0
    # Scale (threshold..1.0) → (0..1)
    return min(1.0, (share - threshold) / max(1e-9, (1.0 - threshold)))


def detect_wash_trade_score(txs: Iterable[dict]) -> dict:
    """
    Primary entry point.

    Args:
        txs: iterable of Birdeye-style tx dicts

    Returns:
        {
          "score": float 0..1,          # 0 = healthy, 1 = full wash
          "insufficient_data": bool,    # true when below min_txs_required
          "components": {
              "rapid_flip": float,
              "low_unique_ratio": float,
              "volume_concentration": float,
          },
          "stats": {"total_txs": int, "unique_wallets": int, "total_volume_usd": float},
        }
    """
    cfg = _get_cfg()
    weights = cfg.get("weights") or {}
    w_flip = float(weights.get("same_wallet_rapid_flip", 0.40))
    w_unique = float(weights.get("low_unique_ratio", 0.30))
    w_conc = float(weights.get("volume_concentration", 0.30))
    window_sec = int(cfg.get("window_sec", 60))
    unique_threshold = float(cfg.get("unique_ratio_threshold", 0.30))
    conc_threshold = float(cfg.get("concentration_threshold", 0.50))
    min_txs = int(cfg.get("min_txs_required", 10))

    norm: list[dict] = []
    for tx in txs or []:
        n = _normalise_tx(tx)
        if n is not None:
            norm.append(n)

    stats = {
        "total_txs": len(norm),
        "unique_wallets": len({t["wallet"] for t in norm}),
        "total_volume_usd": round(sum(t["volume_usd"] for t in norm), 2),
    }

    if len(norm) < min_txs:
        return {
            "score": 0.0,
            "insufficient_data": True,
            "components": {"rapid_flip": 0.0, "low_unique_ratio": 0.0, "volume_concentration": 0.0},
            "stats": stats,
        }

    flip = _rapid_flip_ratio(norm, window_sec)
    uniq = _unique_ratio_penalty(norm, unique_threshold)
    conc = _concentration_penalty(norm, conc_threshold)

    score = w_flip * flip + w_unique * uniq + w_conc * conc
    score = max(0.0, min(1.0, score))

    return {
        "score": round(score, 3),
        "insufficient_data": False,
        "components": {
            "rapid_flip": round(flip, 3),
            "low_unique_ratio": round(uniq, 3),
            "volume_concentration": round(conc, 3),
        },
        "stats": stats,
    }
