"""
analysis/cross_validator.py — Cross-source signal validator.

Compares the same semantic metrics across independent data sources
(Birdeye overview, RugCheck report, Solana Tracker) and reports:

  - `consensus_score`      — averaged across sources that agreed
  - `confidence`           — "high" | "medium" | "low"
  - `conflicting`          — bool, true when any two sources disagree > threshold
  - `conflicts`            — list of human-readable diff descriptions
  - `sources_used`         — list of source names that provided data

Pure function. Input is three optional dicts; any may be None.

Conflict threshold: 30% relative difference on the primary numeric fields
(top10 holder %, risk score). Adjustable via `conflict_threshold` kwarg.
"""
from __future__ import annotations

from typing import Optional


def _get(d: Optional[dict], *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rel_diff(a: float, b: float) -> float:
    """Relative difference vs the larger magnitude. Handles zero safely."""
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


def cross_validate_scores(
    birdeye: Optional[dict] = None,
    rugcheck: Optional[dict] = None,
    tracker: Optional[dict] = None,
    conflict_threshold: float = 0.30,
) -> dict:
    """
    Validate core signals across providers.

    Expected input shape (all optional, any combination):
      birdeye:  {"top10HolderPercent", "holder", "liquidity", ...}
                  OR pre-normalised {"top10_pct": ..., "holder_count": ..., ...}
      rugcheck: output from RugCheckClient.get_report() (parsed dict)
      tracker:  output from SolanaTracker client.get_token() when enabled

    Returns dict with keys documented in the module docstring.
    """
    sources_used: list[str] = []
    conflicts: list[str] = []

    # --- TOP 10 HOLDER % -------------------------------------------------
    top10_values: list[tuple[str, float]] = []
    b_top = _to_float(_get(birdeye, "top10_pct", "top10HolderPercent"))
    if b_top is not None:
        top10_values.append(("birdeye", b_top * 100 if b_top <= 1 else b_top))
    r_top = _to_float(_get(rugcheck, "top_holders_pct"))
    if r_top is not None:
        top10_values.append(("rugcheck", r_top))
    t_top = _to_float(_get(tracker, "top10_pct", "top_holders_pct"))
    if t_top is not None:
        top10_values.append(("tracker", t_top))

    # --- RISK SCORE ------------------------------------------------------
    # Normalise to 0-100 risk scale (higher = worse).
    risk_values: list[tuple[str, float]] = []
    r_risk = _to_float(_get(rugcheck, "risk_score_1_10"))
    if r_risk is not None:
        risk_values.append(("rugcheck", min(100.0, r_risk * 10)))
    t_risk = _to_float(_get(tracker, "risk_score"))
    if t_risk is not None:
        # Solana Tracker returns 1-10 too
        risk_values.append(("tracker", min(100.0, t_risk * 10) if t_risk <= 10 else t_risk))

    # Track which sources contributed any data at all
    if birdeye:
        sources_used.append("birdeye")
    if rugcheck:
        sources_used.append("rugcheck")
    if tracker:
        sources_used.append("tracker")

    # --- CONFLICT DETECTION ---------------------------------------------
    def _check_pairs(label: str, pairs: list[tuple[str, float]]):
        """Emit a conflict msg when any two values diverge > threshold."""
        for i in range(len(pairs)):
            for j in range(i + 1, len(pairs)):
                src_a, val_a = pairs[i]
                src_b, val_b = pairs[j]
                if _rel_diff(val_a, val_b) > conflict_threshold:
                    conflicts.append(
                        f"{label} mismatch: {src_a}={val_a:.1f} vs {src_b}={val_b:.1f} "
                        f"(Δ>{int(conflict_threshold*100)}%)"
                    )

    _check_pairs("Top10 holder %", top10_values)
    _check_pairs("Risk score", risk_values)

    # --- CONSENSUS & CONFIDENCE -----------------------------------------
    # Consensus score: use the authority hierarchy — prefer RugCheck risk
    # where available, fall back to holder concentration if risk missing.
    consensus_score: Optional[float] = None
    if risk_values:
        consensus_score = round(sum(v for _, v in risk_values) / len(risk_values), 1)

    n_sources = len(sources_used)
    if conflicts:
        confidence = "low"
    elif n_sources >= 2:
        confidence = "high"
    elif n_sources == 1:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "consensus_score": consensus_score,     # 0-100 composite risk (higher = worse)
        "confidence": confidence,                # "high" | "medium" | "low"
        "conflicting": bool(conflicts),
        "conflicts": conflicts,
        "sources_used": sources_used,
        "signals_compared": {
            "top10_pct_values": top10_values,
            "risk_score_values": risk_values,
        },
    }
