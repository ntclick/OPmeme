# database/models.py — SQLAlchemy ORM models

from sqlalchemy import (
    Column, Integer, Text, Float, Boolean, DateTime, ForeignKey,
)

from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Coin(Base):
    __tablename__ = "coins"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(Text, nullable=False, index=True)
    name = Column(Text)
    contract_address = Column(Text, unique=True, index=True)
    chain = Column(Text, default="solana")
    logo_url = Column(Text)
    total_supply = Column(Float)
    decimals = Column(Integer, default=9)
    is_verified = Column(Boolean, default=False)
    first_seen_at = Column(DateTime, server_default=func.now())
    last_checked_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    tweets = relationship("TweetRaw", back_populates="coin", cascade="all, delete-orphan")
    reports = relationship("AnalysisReport", back_populates="coin", cascade="all, delete-orphan")
    holder_snapshots = relationship("HolderSnapshot", back_populates="coin", cascade="all, delete-orphan")


class TweetRaw(Base):
    __tablename__ = "tweets_raw"

    id = Column(Integer, primary_key=True, autoincrement=True)
    coin_id = Column(Integer, ForeignKey("coins.id", ondelete="CASCADE"), nullable=False)
    tweet_id = Column(Text, unique=True)
    author_username = Column(Text)
    author_name = Column(Text)
    author_followers = Column(Integer, default=0)
    author_verified = Column(Boolean, default=False)
    full_text = Column(Text, nullable=False)
    retweet_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    quote_count = Column(Integer, default=0)
    is_retweet = Column(Boolean, default=False)
    is_quote = Column(Boolean, default=False)
    language = Column(Text, default="en")
    tweet_created_at = Column(DateTime)
    scraped_at = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())

    coin = relationship("Coin", back_populates="tweets")


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    coin_id = Column(Integer, ForeignKey("coins.id", ondelete="CASCADE"), nullable=False)

    # Overall scores
    overall_score = Column(Integer, nullable=False)
    risk_level = Column(Text, nullable=False)

    # Component scores
    social_score = Column(Integer)
    bot_score = Column(Integer)
    sentiment_score = Column(Integer)
    holder_score = Column(Integer)

    # AI results
    verdict = Column(Text)
    red_flags = Column(Text)          # JSON string
    green_flags = Column(Text)        # JSON string
    detailed_analysis = Column(Text)

    # Verification (OpenGradient)
    tx_hash = Column(Text)
    model_cid = Column(Text)
    inference_proof = Column(Text)    # JSON string

    # Metadata
    tweet_count_analyzed = Column(Integer)
    analysis_duration_ms = Column(Integer)
    created_at = Column(DateTime, server_default=func.now())

    coin = relationship("Coin", back_populates="reports")
    holder_snapshot = relationship("HolderSnapshot", back_populates="report", uselist=False)


class HolderSnapshot(Base):
    __tablename__ = "holder_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    coin_id = Column(Integer, ForeignKey("coins.id", ondelete="CASCADE"), nullable=False)
    report_id = Column(Integer, ForeignKey("analysis_reports.id", ondelete="SET NULL"))

    total_holders = Column(Integer)
    top10_percentage = Column(Float)
    top20_percentage = Column(Float)
    dev_wallet_pct = Column(Float)
    liquidity_locked = Column(Boolean)
    lp_burn_percentage = Column(Float)
    snapshot_data = Column(Text)      # JSON string

    created_at = Column(DateTime, server_default=func.now())

    coin = relationship("Coin", back_populates="holder_snapshots")
    report = relationship("AnalysisReport", back_populates="holder_snapshot")


class WatchedCoin(Base):
    __tablename__ = "watched_coins"

    mint = Column(Text, primary_key=True)
    symbol = Column(Text, nullable=False, index=True)
    name = Column(Text)
    dex = Column(Text)                    # Raydium, Orca, Meteora, Pump.fun, etc.
    status = Column(Text, nullable=False, default="active")  # active|degraded|removed
    score = Column(Integer, default=0)
    mcap = Column(Float, default=0)
    liq = Column(Float, default=0)
    holders = Column(Integer, default=0)
    smart_money = Column(Text)          # JSON array of wallet addresses
    pump_created_at = Column(Integer)    # unix timestamp — khi token được tạo trên pump.fun
    lp_added_at = Column(Integer)       # unix timestamp — khi LP được tạo trên DEX (Raydium, Orca...)
    last_checked = Column(Integer)      # unix timestamp
    degraded_at = Column(Integer)       # unix timestamp, nullable
    filter_t0 = Column(Boolean, default=False)
    filter_t1 = Column(Boolean, default=False)
    filter_t2 = Column(Boolean, default=False)
    filter_t3 = Column(Boolean, default=False)
    filter_t4 = Column(Boolean, default=False)
    birdeye_checked = Column(Boolean, default=False)
    deep_scanned = Column(Boolean, default=False)     # True after Tier 2 (T1-T4) completes
    ai_analysis = Column(Text)                         # JSON string of AI verdict
    ai_scanned_at = Column(Integer)                    # unix timestamp of last AI analysis
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    events = relationship("EventLog", back_populates="watched_coin", cascade="all, delete-orphan")


class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint = Column(Text, ForeignKey("watched_coins.mint", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(Text)
    event_type = Column(Text, nullable=False)  # new|ok|sm|out|warn|dead
    message = Column(Text)
    score = Column(Integer)
    created_at = Column(Integer, nullable=False)  # unix timestamp

    watched_coin = relationship("WatchedCoin", back_populates="events")


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    cache_key = Column(Text, primary_key=True)
    cache_value = Column(Text, nullable=False)  # JSON serialized
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
