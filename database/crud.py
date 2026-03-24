# database/crud.py — CRUD operations for coins, tweets, reports

from sqlalchemy.orm import Session
from database.models import Coin, TweetRaw, AnalysisReport, HolderSnapshot, WatchedCoin, EventLog
from datetime import datetime
from typing import Optional
import json
import logging
import time

logger = logging.getLogger(__name__)


def get_or_create_coin(
    db: Session,
    symbol: str,
    contract_address: Optional[str] = None,
) -> Coin:
    """
    Get existing coin by symbol/contract_address, or create a new one.
    If contract_address is provided, prefer matching on that (it's unique).
    """
    coin = None

    # Try by contract address first (more precise)
    if contract_address:
        coin = db.query(Coin).filter(Coin.contract_address == contract_address).first()

    # Fallback to symbol
    if not coin:
        coin = db.query(Coin).filter(Coin.symbol == symbol.upper()).first()

    # Create new
    if not coin:
        coin = Coin(
            symbol=symbol.upper(),
            contract_address=contract_address,
        )
        db.add(coin)
        db.commit()
        db.refresh(coin)
        logger.info(f"Created new coin: {coin.symbol} (id={coin.id})")

    return coin


def _parse_tweet_date(date_str: str) -> datetime:
    """Parse tweet date with multiple format support."""
    if not date_str:
        return datetime.utcnow()
    
    # 1. Try ISO format
    try:
        if isinstance(date_str, str):
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        pass
    
    # 2. Try Nitter format: "Feb 20, 2026 · 11:56 AM UTC"
    try:
        clean = date_str.replace("UTC", "").strip()
        for char in ["·", "•", "·", " · "]:
            clean = clean.replace(char, " - ")
        clean = " ".join(clean.split())
        return datetime.strptime(clean, "%b %d, %Y - %I:%M %p")
    except ValueError:
        pass
    
    return datetime.utcnow()


def save_tweets(db: Session, coin_id: int, tweets: list[dict]) -> int:
    """
    Save tweets to DB. Skips duplicates by tweet_id.
    Returns: number of new tweets saved.
    """
    saved_count = 0
    seen_ids = set()

    for t in tweets:
        tweet_id = t["tweet_id"]
        
        if tweet_id in seen_ids:
            continue
        seen_ids.add(tweet_id)

        # Check duplicate in DB
        existing = db.query(TweetRaw).filter_by(tweet_id=tweet_id).first()
        if existing:
            continue

        tweet_record = TweetRaw(
            coin_id=coin_id,
            tweet_id=tweet_id,
            author_username=t["author_username"],
            author_name=t["author_name"],
            author_followers=t["author_followers"],
            author_verified=t["author_verified"],
            full_text=t["full_text"],
            retweet_count=t["retweet_count"],
            like_count=t["like_count"],
            reply_count=t["reply_count"],
            quote_count=t["quote_count"],
            is_retweet=t["is_retweet"],
            is_quote=t["is_quote"],
            language=t["language"],
            tweet_created_at=None,
        )
        # Safe date parsing with multiple formats
        tweet_record.tweet_created_at = _parse_tweet_date(t.get("tweet_created_at"))

        db.add(tweet_record)
        saved_count += 1

    db.commit()
    return saved_count


def save_holder_snapshot(
    db: Session,
    coin_id: int,
    report_id: Optional[int],
    holder_data: dict,
) -> HolderSnapshot:
    """Save a holder snapshot linked to a coin and optionally a report."""
    import json

    snapshot = HolderSnapshot(
        coin_id=coin_id,
        report_id=report_id,
        total_holders=holder_data.get("total_holders"),
        top10_percentage=holder_data.get("top10_pct"),
        top20_percentage=holder_data.get("top20_pct"),
        dev_wallet_pct=holder_data.get("dev_wallet_pct"),
        liquidity_locked=holder_data.get("liquidity_locked"),
        lp_burn_percentage=holder_data.get("lp_burned_pct"),
        snapshot_data=json.dumps(holder_data.get("holders", [])),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


# ── WatchedCoin & EventLog CRUD ───────────────────────────────────

def get_all_watched(db: Session) -> list[dict]:
    """Return all watched coins with their events, formatted for API response."""
    coins = db.query(WatchedCoin).all()
    result = []
    for c in coins:
        events = db.query(EventLog).filter(EventLog.mint == c.mint).order_by(EventLog.created_at.asc()).all()
        sm = json.loads(c.smart_money) if c.smart_money else []
        result.append({
            "mint": c.mint,
            "symbol": c.symbol,
            "name": c.name,
            "dex": c.dex,
            "status": c.status,
            "score": c.score,
            "mcap": c.mcap,
            "liq": c.liq,
            "holders": c.holders,
            "smart_money": sm,
            "pump_created_at": c.pump_created_at,
            "lp_added_at": c.lp_added_at,
            "last_checked": c.last_checked,
            "degraded_at": c.degraded_at,
            "filter": {
                "t0": c.filter_t0,
                "t1": c.filter_t1,
                "t2": c.filter_t2,
                "t3": c.filter_t3,
                "t4": c.filter_t4,
            },
            "birdeye_checked": c.birdeye_checked,
            "events": [
                {"type": e.event_type, "msg": e.message, "ts": e.created_at, "score": e.score}
                for e in events
            ],
        })
    return result


def get_events(db: Session, limit: int = 500) -> list[dict]:
    """Return recent events across all coins."""
    events = db.query(EventLog).order_by(EventLog.created_at.desc()).limit(limit).all()
    return [
        {
            "type": e.event_type,
            "msg": e.message,
            "ts": e.created_at,
            "score": e.score,
            "mint": e.mint,
            "symbol": e.symbol,
        }
        for e in events
    ]


def get_dashboard_stats(db: Session) -> dict:
    """Return aggregate stats for the dashboard."""
    coins = db.query(WatchedCoin).all()
    total = len(coins)
    active = sum(1 for c in coins if c.status == "active")
    removed = sum(1 for c in coins if c.status == "removed")
    sm = sum(1 for c in coins if c.smart_money and json.loads(c.smart_money))
    pass_t3 = sum(1 for c in coins if c.filter_t0 and c.filter_t1 and c.filter_t2 and c.filter_t3)
    birdeye_called = sum(1 for c in coins if c.birdeye_checked)
    now = int(time.time())
    ages = [now - (c.lp_added_at or now) for c in coins]
    avg_age = sum(ages) / len(ages) if ages else 0
    return {
        "total": total,
        "active": active,
        "removed": removed,
        "smart_money": sm,
        "pass_t3": pass_t3,
        "birdeye_called": birdeye_called,
        "birdeye_saved": total - birdeye_called,
        "avg_age_seconds": avg_age,
    }


def log_event(db: Session, mint: str, symbol: str, event_type: str, message: str, score: int = None):
    """Insert an event into event_log."""
    evt = EventLog(
        mint=mint,
        symbol=symbol,
        event_type=event_type,
        message=message,
        score=score,
        created_at=int(time.time()),
    )
    db.add(evt)
    db.commit()
    return evt
