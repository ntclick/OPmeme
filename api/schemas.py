# api/schemas.py — Pydantic request/response models

from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime


class AnalyzeRequest(BaseModel):
    """Request to analyze a coin.

    Supports two inputs:
    1. Ticker only:             {"ticker": "BONK"}
    2. Contract address only:   {"ticker": "DezXAZ8z7Pn..."}  → auto-detected
    3. Both:                    {"ticker": "BONK", "contract_address": "DezX..."}
    """
    ticker: str = Field(
        ..., min_length=1, max_length=50,
        description="Coin symbol (e.g. PEPE) or Solana contract address"
    )
    contract_address: Optional[str] = Field(
        None, description="Solana mint address (if known)"
    )
    force_refresh: bool = Field(
        False, description="Skip cache and re-analyze from scratch"
    )


class ScoreBreakdown(BaseModel):
    social_score: int
    bot_score: int
    sentiment_score: int
    holder_score: int
    ai_trust_score: Optional[int] = None


class AnalyzeResponse(BaseModel):
    """Response after analysis is complete."""
    ticker: str
    contract_address: Optional[str] = None

    # Main scores
    overall_score: int = Field(..., ge=0, le=100)
    risk_level: str       # LOW / MEDIUM / HIGH / EXTREME / MOON

    # Breakdown
    scores: ScoreBreakdown
    verdict: str          # Short judgment sentence
    red_flags: list[str]
    green_flags: list[str]

    # Verification
    tx_hash: Optional[str] = None       # OpenGradient proof
    verify_url: Optional[str] = None    # Link to check proof on explorer

    # Metadata
    tweets_analyzed: int
    analyzed_at: datetime
    cached: bool = False
    opengradient_used: bool = False
    algorithm_notes: list[str] = Field(default_factory=list)
    data_sources: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


class ReportSummary(BaseModel):
    id: int
    overall_score: int
    risk_level: str
    verdict: Optional[str] = None
    tx_hash: Optional[str] = None
    analyzed_at: datetime


class HistoryResponse(BaseModel):
    ticker: str
    reports: list[ReportSummary]


class TrendingItem(BaseModel):
    ticker: str
    checks_24h: int
    avg_score: Optional[float] = None


class TrendingResponse(BaseModel):
    trending: list[TrendingItem]
