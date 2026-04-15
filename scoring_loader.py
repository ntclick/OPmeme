"""
scoring_loader.py — YAML-driven scoring thresholds loader.

Loaded once at startup. Call `get_thresholds()` to access the parsed dict.
Use `tier_score(value, tiers, scores, below_min)` for the common
"descending-tier lookup" pattern used throughout the audit pipeline.

NOTE: This module is intentionally a top-level module (NOT a package) to
avoid colliding with the existing root `config.py`. The YAML file lives at
`config/scoring_thresholds.yaml` — a pure data directory without __init__.py.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parent / "config" / "scoring_thresholds.yaml"
_CACHE: dict[str, Any] | None = None


def _resolve_path() -> Path:
    env_path = os.getenv("SCORING_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return _DEFAULT_PATH


def get_thresholds(force_reload: bool = False) -> dict[str, Any]:
    """Return parsed thresholds. Loaded once, cached in-process."""
    global _CACHE
    if _CACHE is not None and not force_reload:
        return _CACHE

    path = _resolve_path()
    if not path.exists():
        logger.warning("Scoring config not found at %s — using empty dict", path)
        _CACHE = {}
        return _CACHE

    try:
        with open(path, "r", encoding="utf-8") as fh:
            _CACHE = yaml.safe_load(fh) or {}
        logger.info("Loaded scoring thresholds v%s from %s",
                    _CACHE.get("version", "?"), path)
    except Exception as e:
        logger.exception("Failed to load scoring config from %s: %s", path, e)
        _CACHE = {}
    return _CACHE


def reload_thresholds() -> dict[str, Any]:
    """Force reload (for tests or hot-reload scenarios)."""
    return get_thresholds(force_reload=True)


def tier_score(
    value: Optional[float],
    tiers: Sequence[float],
    scores: Sequence[int],
    below_min: int = 0,
    unknown: int = 50,
    descending: bool = True,
) -> int:
    """
    Lookup the first tier that `value` meets-or-exceeds.

    Assumes tiers are sorted descending (e.g. [5M, 1M, 500K, ...]) and the
    corresponding `scores` array aligns positionally.

    Set `descending=False` when tiers are ascending (e.g. volatility where
    lower is safer).

    Returns `unknown` when value is None, `below_min` when value is below
    all tiers.
    """
    if value is None:
        return unknown
    if len(tiers) != len(scores):
        raise ValueError(f"tiers/scores length mismatch: {len(tiers)} vs {len(scores)}")

    if descending:
        for t, s in zip(tiers, scores):
            if value >= t:
                return int(s)
    else:
        for t, s in zip(tiers, scores):
            if value <= t:
                return int(s)
    return int(below_min)


def get_weights() -> dict[str, float]:
    """Convenience: top-level weights dict."""
    return get_thresholds().get("weights", {})


def get_section(name: str) -> dict[str, Any]:
    """Return a named section (liquidity, holders, security, ...)."""
    return get_thresholds().get(name, {}) or {}


def get_risk_level(score: int) -> str:
    """Map numeric score to risk level label."""
    levels = get_thresholds().get("risk_levels", {}) or {}
    if score >= levels.get("MOON", 80): return "MOON"
    if score >= levels.get("LOW", 65): return "LOW"
    if score >= levels.get("MEDIUM", 50): return "MEDIUM"
    if score >= levels.get("HIGH", 35): return "HIGH"
    if score >= levels.get("EXTREME", 20): return "EXTREME"
    return "AVOID"
