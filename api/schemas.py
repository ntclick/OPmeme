# api/schemas.py — Pydantic request/response models (v3.0)

from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime


class AnalyzeRequest(BaseModel):
    """Request to analyze a coin."""
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

    llm_model: Optional[str] = Field(
        None,
        description="OpenGradient TEE_LLM model enum name (e.g. CLAUDE_SONNET_4_5). If not set, server default is used.",
    )


class ScoreBreakdown(BaseModel):
    """Individual component scores."""
    # Social (15%)
    social_score: Optional[int] = Field(default=None, ge=0, le=100)
    bot_score: Optional[int] = Field(default=None, ge=0, le=100)
    sentiment_score: Optional[int] = Field(default=None, ge=0, le=100)
    
    # On-Chain (30%)
    holder_score: int = Field(default=50, ge=0, le=100)
    security_score: Optional[int] = Field(default=50, ge=0, le=100)
    
    # Technical (55%)
    momentum_score: Optional[int] = Field(default=50, ge=0, le=100)
    volume_score: Optional[int] = Field(default=50, ge=0, le=100)
    liquidity_score: Optional[int] = Field(default=50, ge=0, le=100)
    
    # AI
    ai_trust_score: Optional[int] = Field(default=None, ge=0, le=100)


class ComponentDetail(BaseModel):
    """Detail for a single component."""
    score: int = Field(default=50, ge=0, le=100)
    weight: str = "0%"
    details: list[str] = Field(default_factory=list)


class TechnicalComponents(BaseModel):
    """Technical analysis components (55% weight)."""
    momentum: Optional[ComponentDetail] = None
    volume: Optional[ComponentDetail] = None
    trade_pressure: Optional[ComponentDetail] = None
    liquidity: Optional[ComponentDetail] = None


class OnChainComponents(BaseModel):
    """On-chain analysis components (30% weight)."""
    holder: Optional[ComponentDetail] = None
    security: Optional[ComponentDetail] = None
    whale_risk: Optional[ComponentDetail] = None


class SocialComponents(BaseModel):
    """Social analysis components (15% weight)."""
    community: Optional[ComponentDetail] = None
    bot_detect: Optional[ComponentDetail] = None


class CategoryBreakdown(BaseModel):
    """Breakdown for a category (technical/onchain/social)."""
    total: float = 0
    weight: str = "0%"
    components: dict[str, Any] = Field(default_factory=dict)


class ExtendedBreakdown(BaseModel):
    """Extended breakdown for v3.0 UI."""
    technical: Optional[CategoryBreakdown] = None
    onchain: Optional[CategoryBreakdown] = None
    social: Optional[CategoryBreakdown] = None


class TechnicalSignals(BaseModel):
    """Technical indicators from analysis."""
    rsi: Optional[float] = None
    rsi_signal: Optional[str] = None
    divergence: Optional[str] = None
    trend: Optional[str] = None
    buy_sell_ratio: Optional[float] = None


class SignalsSummary(BaseModel):
    """Bullish/Bearish/Neutral signals."""
    bullish: list[str] = Field(default_factory=list)
    bearish: list[str] = Field(default_factory=list)
    neutral: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    """Response after analysis is complete."""
    ticker: str
    contract_address: Optional[str] = None

    # Main scores
    overall_score: int = Field(..., ge=0, le=100)
    trust_score: int = Field(..., ge=0, le=100)
    risk_level: str  # LOW / MEDIUM / HIGH / EXTREME / MOON / AVOID

    # Score breakdowns
    scores: ScoreBreakdown
    breakdown: Optional[dict[str, Any]] = None  # Extended breakdown

    market_data: Optional[dict[str, Any]] = None
    
    # Signals
    signals: Optional[SignalsSummary] = None
    technical_indicators: Optional[TechnicalSignals] = None
    
    # Verdict & Flags
    verdict: str
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)

    # Verification
    tx_hash: Optional[str] = None
    payment_hash: Optional[str] = None
    settlement_mode: Optional[str] = None
    verification: Optional[dict[str, Any]] = None
    verify_url: Optional[str] = None

    # Metadata
    tweets_analyzed: int = 0
    analyzed_at: datetime
    cached: bool = False
    opengradient_used: bool = False
    model_used: Optional[str] = None
    algorithm_notes: list[str] = Field(default_factory=list)
    data_sources: dict[str, Any] = Field(default_factory=dict)
    force_refresh: bool = False


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
