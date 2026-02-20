# api/routes.py — FastAPI API endpoints

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func as sql_func
from datetime import datetime, timedelta
import time
import json
# Trigger reload (installed bs4)
import logging
import httpx
from pydantic import BaseModel

from api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    HistoryResponse,
    ReportSummary,
    TrendingResponse,
    TrendingItem,
    ScoreBreakdown,
)
from database.engine import get_db
from database.models import Coin, AnalysisReport
from database import crud
from scrapers.twitter_scraper import TwitterScraper
from scrapers.solana_scraper import SolanaScraper
from analysis.scoring import (
    calculate_social_score,
    calculate_holder_score,
    calculate_overall_score,
)
from analysis.bot_detector import calculate_bot_score
from analysis.sentiment import calculate_sentiment_score
from analysis.opengradient import OpenGradientAnalyzer
from utils.cache import check_cache, set_cache


logger = logging.getLogger(__name__)

router = APIRouter()

# Instantiate services
twitter_scraper = TwitterScraper()
solana_scraper = SolanaScraper()
og_analyzer = OpenGradientAnalyzer()

CACHE_TTL_MINUTES = 15

class DexSearchRequest(BaseModel):
    query: str

@router.get("/api/dex/tokens/{addresses}")
async def proxy_dex_tokens(addresses: str):
    """
    Proxy DexScreener Tokens API (for trending/multiple addresses).
    """
    url = f"https://api.dexscreener.com/latest/dex/tokens/{addresses}"
    logger.info(f"🔍 [DexScreener] Proxying tokens: {addresses[:20]}...")
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"✅ [DexScreener] Found {len(data.get('pairs', []))} pairs")
            return data
        except Exception as e:
            logger.error(f"❌ [DexScreener] Tokens proxy failed: {e}")
            raise HTTPException(status_code=502, detail="Failed to fetch tokens")

@router.get("/api/dex/search")
async def proxy_dex_search(q: str):
    """
    Proxy DexScreener API requests to:
    1. Avoid client-side CORS/Network issues.
    2. Provide server-side logging of external interactions.
    """
    url = f"https://api.dexscreener.com/latest/dex/search?q={q}"
    logger.info(f"🔍 [DexScreener] Proxying search for: {q}")
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"✅ [DexScreener] Found {len(data.get('pairs', []))} pairs")
            return data
        except Exception as e:
            logger.error(f"❌ [DexScreener] Proxy failed: {e}")
            raise HTTPException(status_code=502, detail="Failed to fetch from DexScreener")

@router.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_coin(request: AnalyzeRequest, db: Session = Depends(get_db)):
    """
    MAIN ENDPOINT: Analyze a meme coin.

    Flow:
    1. Check cache (result < 15 min old → return immediately)
    2. Scrape tweets from Apify
    3. Fetch on-chain data from Solana RPC
    4. Run scoring algorithms
    5. Call OpenGradient AI (verifiable)
    6. Compile results, save to DB, return response

    Estimated time: 10–30 seconds (depends on Apify + OpenGradient)
    """
    ticker = request.ticker.strip().strip("$#")
    contract_address = request.contract_address
    start_time = time.time()

    # Auto-detect: if ticker looks like a Solana address, swap it
    import re
    # DEBUG LOG
    logger.info(f"Incoming ticker input: '{ticker}' (len: {len(ticker)})")

    # Relaxed detection: if > 30 chars, assume it's an address
    if len(ticker) > 30 and " " not in ticker:
        contract_address = contract_address or ticker
        logger.info(f"Detected contract address: {contract_address}")
        
        # Try to resolve symbol via DexScreener
        try:
            resolved_symbol = await solana_scraper.get_token_symbol(contract_address)
            if resolved_symbol:
                ticker = resolved_symbol.upper()
            else:
                ticker = ticker[:8].upper()
        except:
            ticker = ticker[:8].upper()
    else:
        ticker = ticker.upper()

    # ── STEP 1: Check cache ──
    if not request.force_refresh:
        cached = check_cache(f"analysis:{ticker}", CACHE_TTL_MINUTES)
        if cached:
            cached["cached"] = True
            logger.info(f"Analyze cache hit for {ticker}")
            return AnalyzeResponse(**cached)

    try:
        # ── STEP 2: Ensure coin exists in DB ──
        coin = crud.get_or_create_coin(db, ticker, contract_address)

        # ── STEP 3: Scrape Twitter ──
        tweets = []
        try:
            tweets = twitter_scraper.scrape_by_ticker(ticker, max_tweets=100)
        except Exception as scrape_err:
            logger.warning(f"Twitter scraping failed for {ticker}: {scrape_err}")

        # Save tweets to DB (if any)
        if tweets:
            crud.save_tweets(db, coin.id, tweets)

        # ── STEP 4: Get Solana on-chain data ──
        on_chain_data = {}
        if contract_address:
            token_info = await solana_scraper.get_token_info(
                contract_address
            )
            holder_data = await solana_scraper.get_top_holders(
                contract_address
            )
            on_chain_data = {
                "token_info": token_info,
                "holders": holder_data,
            }

            # If we found a real symbol from on-chain data, use it
            if token_info and token_info.get("symbol"):
                real_symbol = token_info["symbol"].upper().strip()
                if len(real_symbol) < 20:  # Sanity check
                    ticker = real_symbol

        # ── STEP 5: Run scoring algorithms ──
        social_result = calculate_social_score(tweets)
        bot_result = calculate_bot_score(tweets)
        holder_result = calculate_holder_score(
            on_chain_data.get("holders"),
            on_chain_data.get("token_info"),
        )

        # Sentiment: use rule-based as initial proxy
        sentiment_result = calculate_sentiment_score(tweets)
        sentiment_proxy = sentiment_result["sentiment_score"]

        overall_result = calculate_overall_score(
            social_result,
            bot_result,
            sentiment_proxy,
            holder_result,
            token_info=on_chain_data.get("token_info"),
        )

        # ── STEP 6: OpenGradient Verifiable AI ──
        og_result = og_analyzer.analyze_coin(
            ticker=ticker,
            tweets_summary=tweets[:20],
            on_chain_data=on_chain_data,
            scoring_result={
                "social_score": social_result["social_score"],
                "bot_score": bot_result["bot_score"],
                "holder_score": holder_result["holder_score"],
            },
        )

        # Extract AI results — AI is the PRIMARY scoring authority
        ai = og_result.get("ai_result")
        sentiment_final = sentiment_proxy
        ai_trust = None

        if ai:
            # Extract ai_trust_score (new holistic score from AI)
            if ai.get("ai_trust_score") is not None:
                ai_trust = ai["ai_trust_score"]
            # Also keep sentiment for display
            if ai.get("ai_sentiment_score") is not None:
                sentiment_final = ai["ai_sentiment_score"]

        # Re-calculate overall with AI trust score (40% weight when available)
        overall_result = calculate_overall_score(
            social_result, 
            bot_result, 
            sentiment_final, 
            holder_result,
            ai_trust_score=ai_trust,
            token_info=on_chain_data.get("token_info"),
        )

        opengradient_used = bool(og_result.get("used_opengradient"))
        if not opengradient_used:
            logger.warning(
                f"OpenGradient verification not used for {ticker}. "
                f"Reason: {og_result.get('error', 'unknown')}"
            )

        # ── STEP 7: Compile red/green flags ──
        all_red_flags: list[str] = []
        all_green_flags: list[str] = []

        # From bot detector
        if bot_result.get("bot_patterns_found"):
            all_red_flags.extend(
                f
                for f in bot_result["bot_patterns_found"]
                if "No bot" not in f
            )

        # From holder analysis
        if holder_result.get("red_flags"):
            all_red_flags.extend(holder_result["red_flags"])

        # From AI
        if ai:
            all_red_flags.extend(ai.get("red_flags", []))
            all_green_flags.extend(ai.get("green_flags", []))

        # Auto green flags based on scores
        if social_result["social_score"] > 70:
            all_green_flags.append("Strong organic community")
        if bot_result["bot_score"] > 80:
            all_green_flags.append("Low bot activity")
        if holder_result.get("mint_renounced"):
            all_green_flags.append("Mint authority renounced ✓")

        # Deduplicate while preserving order
        all_red_flags = list(dict.fromkeys(all_red_flags))
        all_green_flags = list(dict.fromkeys(all_green_flags))

        # ── STEP 8: Build verdict ──
        verdict = (
            ai.get("verdict", "")
            if ai and ai.get("verdict")
            else f"${ticker}: Score {overall_result['overall_score']}/100. "
                 f"Risk: {overall_result['risk_level']}. DYOR."
        )

        # ── STEP 9: Save to DB ──
        duration_ms = int((time.time() - start_time) * 1000)

        report = AnalysisReport(
            coin_id=coin.id,
            overall_score=overall_result["overall_score"],
            risk_level=overall_result["risk_level"],
            social_score=social_result["social_score"],
            bot_score=bot_result["bot_score"],
            sentiment_score=sentiment_final,
            holder_score=holder_result["holder_score"],
            verdict=verdict,
            red_flags=json.dumps(all_red_flags),
            green_flags=json.dumps(all_green_flags),
            detailed_analysis=json.dumps(og_result.get("ai_result")),
            tx_hash=og_result.get("tx_hash"),
            model_cid=og_result.get("model_cid"),
            inference_proof=json.dumps(og_result),
            tweet_count_analyzed=len(tweets),
            analysis_duration_ms=duration_ms,
        )
        db.add(report)
        db.commit()

        # Update last_checked
        coin.last_checked_at = datetime.utcnow()
        db.commit()

        # ── STEP 10: Build response ──
        scoring_mode = "AI-first (OpenGradient)" if overall_result.get("ai_driven") else "Algorithmic fallback"
        algorithm_notes = [
            f"Scoring mode: {scoring_mode}.",
        ]
        if overall_result.get("ai_driven"):
            algorithm_notes.append(
                "Overall = AI Trust(40%) + Social(20%) + Bot(15%) + Holder(25%). "
                "AI Trust Score is the primary component, verified on-chain via OpenGradient TEE."
            )
        else:
            algorithm_notes.append(
                "Overall = Social(35%) + Bot(25%) + Sentiment(15%) + Holder(25%). "
                "Fallback mode — OpenGradient AI was unavailable."
            )
        if ai and ai.get("scoring_rationale"):
            algorithm_notes.append(f"AI rationale: {ai['scoring_rationale']}")
        if overall_result.get("override_reasons"):
            algorithm_notes.extend(
                [
                    f"Override applied: {reason}"
                    for reason in overall_result["override_reasons"]
                ]
            )

        has_twitter_real_data = len(tweets) > 0
        has_onchain_real_data = bool(
            on_chain_data.get("token_info") or on_chain_data.get("holders")
        )
        data_sources = {
            "twitter": {
                "provider": "TwitterScraper (Apify-backed)",
                "real_data": has_twitter_real_data,
                "tweets_collected": len(tweets),
                "fallback_default_used": not has_twitter_real_data,
            },
            "solana": {
                "provider": "Solana RPC",
                "contract_address": contract_address,
                "real_data": has_onchain_real_data,
                "token_info_found": bool(on_chain_data.get("token_info")),
                "holder_data_found": bool(on_chain_data.get("holders")),
                "fallback_default_used": contract_address is not None and not has_onchain_real_data,
            },
            "opengradient": {
                "attempted": True,
                "used": opengradient_used,
                "scoring_mode": "AI-first" if overall_result.get("ai_driven") else "fallback",
                "ai_trust_score": ai_trust,
                "tx_hash": og_result.get("tx_hash"),
                "model_used": og_result.get("model_used"),
                "error": og_result.get("error"),
            },
            "synthetic_or_mock_data_used": False,
        }

        if not has_twitter_real_data or (contract_address and not has_onchain_real_data):
            logger.warning(
                f"Analysis for {ticker} completed with partial real data. "
                f"twitter_real={has_twitter_real_data}, onchain_real={has_onchain_real_data}"
            )

        response_data = {
            "ticker": ticker,
            "contract_address": contract_address,
            "overall_score": overall_result["overall_score"],
            "risk_level": overall_result["risk_level"],
            "scores": {
                "social_score": social_result["social_score"],
                "bot_score": bot_result["bot_score"],
                "sentiment_score": sentiment_final,
                "holder_score": holder_result["holder_score"],
                "ai_trust_score": ai_trust,
            },
            "verdict": verdict,
            "red_flags": all_red_flags,
            "green_flags": all_green_flags,
            "tx_hash": og_result.get("tx_hash"),
            "verify_url": (
                f"https://explorer.opengradient.ai/tx/{og_result['tx_hash']}"
                if og_result.get("tx_hash")
                else None
            ),
            "tweets_analyzed": len(tweets),
            "analyzed_at": datetime.utcnow(),
            "cached": False,
            "opengradient_used": opengradient_used,
            "algorithm_notes": algorithm_notes,
            "data_sources": data_sources,
        }

        # Cache the result
        set_cache(f"analysis:{ticker}", response_data, CACHE_TTL_MINUTES)

        logger.info(
            f"Analysis completed for {ticker} in {duration_ms}ms | "
            f"score={overall_result['overall_score']} risk={overall_result['risk_level']} "
            f"og_used={opengradient_used} tx_hash={og_result.get('tx_hash')}"
        )

        return AnalyzeResponse(**response_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Analysis failed for {ticker}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/coin/{ticker}/history", response_model=HistoryResponse)
async def get_analysis_history(
    ticker: str, limit: int = 10, db: Session = Depends(get_db)
):
    """Get the analysis history for a coin."""
    coin = db.query(Coin).filter(Coin.symbol == ticker.upper()).first()
    if not coin:
        raise HTTPException(status_code=404, detail="Coin not found")

    reports = (
        db.query(AnalysisReport)
        .filter(AnalysisReport.coin_id == coin.id)
        .order_by(AnalysisReport.created_at.desc())
        .limit(limit)
        .all()
    )

    return HistoryResponse(
        ticker=ticker.upper(),
        reports=[
            ReportSummary(
                id=r.id,
                overall_score=r.overall_score,
                risk_level=r.risk_level,
                verdict=r.verdict,
                tx_hash=r.tx_hash,
                analyzed_at=r.created_at,
            )
            for r in reports
        ],
    )


@router.get("/api/trending", response_model=TrendingResponse)
async def get_trending(db: Session = Depends(get_db)):
    """Top coins checked most in the last 24 hours."""
    cutoff = datetime.utcnow() - timedelta(hours=24)

    results = (
        db.query(
            Coin.symbol,
            sql_func.count(AnalysisReport.id).label("check_count"),
            sql_func.avg(AnalysisReport.overall_score).label("avg_score"),
        )
        .join(AnalysisReport)
        .filter(AnalysisReport.created_at > cutoff)
        .group_by(Coin.symbol)
        .order_by(sql_func.count(AnalysisReport.id).desc())
        .limit(20)
        .all()
    )

    return TrendingResponse(
        trending=[
            TrendingItem(
                ticker=r.symbol,
                checks_24h=r.check_count,
                avg_score=round(r.avg_score, 1) if r.avg_score else None,
            )
            for r in results
        ]
    )
