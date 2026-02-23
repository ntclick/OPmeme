# api/routes.py — v3.1 Technical Focus (Twitter DISABLED)
# Weights: Technical 65% / On-Chain 35% / Social 0%

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func as sql_func
from datetime import datetime, timedelta
import time
import json
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
from scrapers.solana_scraper import SolanaScraper
from scrapers.evm_scraper import create_evm_scraper
from scrapers.birdeye_client import create_birdeye_client
from analysis.technical_analyzer import TechnicalAnalyzer
from analysis.liquidity_analyzer import LiquidityAnalyzer
from analysis.opengradient import OpenGradientAnalyzer
from utils.cache import cache, CacheKeys, CacheTTL


def _detect_chain(address: str) -> str:
    """Detect blockchain type from address format."""
    if not address:
        return "unknown"
    # EVM: 0x... (42 chars)
    if address.startswith("0x") and len(address) == 42:
        return "evm"
    # Solana: base58, typically 32-44 chars
    if len(address) > 30 and not address.startswith("0x"):
        return "solana"
    return "unknown"


logger = logging.getLogger(__name__)
router = APIRouter()

# Services
solana_scraper = SolanaScraper()
evm_scraper = create_evm_scraper()
birdeye_client = create_birdeye_client()
tech_analyzer = TechnicalAnalyzer()
liquidity_analyzer = LiquidityAnalyzer()
opengradient_analyzer = OpenGradientAnalyzer()


class DexSearchRequest(BaseModel):
    query: str


# ══════════════════════════════════════════════════════════════════════════════
# PROXY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/dex/tokens/{addresses}")
async def proxy_dex_tokens(addresses: str):
    """Proxy DexScreener Tokens API."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{addresses}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"DexScreener proxy failed: {e}")
            raise HTTPException(status_code=502, detail="Failed to fetch tokens")


@router.get("/api/dex/search")
async def proxy_dex_search(q: str):
    """Proxy DexScreener search."""
    url = f"https://api.dexscreener.com/latest/dex/search?q={q}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"DexScreener search failed: {e}")
            raise HTTPException(status_code=502, detail="Failed to search")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ANALYSIS ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_coin(request: AnalyzeRequest, db: Session = Depends(get_db)):
    """
    Analyze a token (Solana or EVM chains).
    
    v3.1: Technical 65% / On-Chain 35% / Social 0% (DISABLED)
    Supports: Solana, Ethereum, Base, BSC
    """
    ticker = request.ticker.strip().strip("$#")
    contract_address = request.contract_address
    force_refresh = getattr(request, 'force_refresh', False)
    start_time = time.time()
    
    # Detect chain type
    chain_type = _detect_chain(contract_address or ticker)
    logger.info(f"📊 ===== ANALYSIS START: '{ticker}' =====")
    logger.info(f"📍 Chain: {chain_type}, ticker: '{ticker}', contract: '{contract_address}', refresh: {force_refresh}")

    # Auto-detect contract address from input
    if len(ticker) > 30 and " " not in ticker and not contract_address:
        contract_address = ticker
        chain_type = _detect_chain(contract_address)
        logger.info(f"📍 Auto-detected contract: {contract_address[:8]}... (chain: {chain_type})")
        
        # Try to resolve ticker from contract
        if chain_type == "solana":
            try:
                resolved = await solana_scraper.get_token_symbol(contract_address)
                ticker = resolved.upper() if resolved else ticker[:8].upper()
            except:
                ticker = ticker[:8].upper()
        elif chain_type == "evm":
            # For EVM, use truncated address as ticker if none provided
            ticker = ticker[:8].upper()
    else:
        ticker = ticker.upper()
        
        # Search DexScreener for contract if not provided
        if not contract_address:
            logger.info(f"🔍 Searching DexScreener for {ticker}...")
            try:
                async with httpx.AsyncClient() as client:
                    search_resp = await client.get(
                        f"https://api.dexscreener.com/latest/dex/search?q={ticker}",
                        timeout=10.0
                    )
                    if search_resp.status_code == 200:
                        search_data = search_resp.json()
                        pairs = search_data.get("pairs", [])
                        
                        # Filter by chain priority: solana, base, eth, bsc
                        for target_chain in ["solana", "base", "ethereum", "bsc"]:
                            chain_pairs = [p for p in pairs if p.get("chainId") == target_chain]
                            if chain_pairs:
                                chain_pairs.sort(key=lambda x: (x.get("liquidity") or {}).get("usd", 0) or 0, reverse=True)
                                best_pair = chain_pairs[0]
                                base_token = best_pair.get("baseToken", {})
                                if base_token.get("symbol", "").upper() == ticker:
                                    contract_address = base_token.get("address")
                                    chain_type = target_chain if target_chain != "ethereum" else "evm"
                                    logger.info(f"✅ Resolved {ticker} → {contract_address[:8]}... ({target_chain})")
                                    break
            except Exception as e:
                logger.error(f"❌ DexScreener search failed: {e}")

    # Check cache
    if not force_refresh:
        cache_key = CacheKeys.full_analysis(contract_address or ticker)
        cached = await cache.get(cache_key)
        if cached:
            cached["cached"] = True
            logger.info(f"✅ Cache hit for {ticker}")
            return AnalyzeResponse(**cached)
    else:
        # Clear AI verdict cache when force refresh
        ai_cache_key = CacheKeys.ai_verdict(ticker)
        await cache.delete(ai_cache_key)
        logger.info(f"🗑️ AI verdict cache cleared for {ticker}")

    try:
        # Ensure coin in DB
        coin = crud.get_or_create_coin(db, ticker, contract_address)

        # ══════════════════════════════════════════════════════════════════════
        # STEP 1: Fetch Data from APIs (Chain-specific)
        # ══════════════════════════════════════════════════════════════════════
        
        birdeye_data = {}
        dex_data = {}
        token_info = {}
        holder_data = {}
        token_age_hours = 0
        
        if contract_address:
            # 1. DexScreener (Universal - works for all chains)
            logger.info(f"� Fetching DexScreener...")
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://api.dexscreener.com/latest/dex/tokens/{contract_address}",
                        timeout=10.0
                    )
                    if resp.status_code == 200:
                        pairs = resp.json().get("pairs", [])
                        if pairs:
                            # Filter by chain
                            chain_pairs = [p for p in pairs if p.get("baseToken", {}).get("address", "").lower() == contract_address.lower()]
                            if chain_pairs:
                                dex_data = max(chain_pairs, key=lambda x: (x.get("liquidity") or {}).get("usd", 0) or 0)
                                # Canonicalize address for case-sensitive chains (Solana base58)
                                base_addr = _safe_get(dex_data, "baseToken", "address")
                                if base_addr and contract_address and chain_type == "solana" and base_addr != contract_address:
                                    logger.info(f"🔧 Normalized Solana address: {contract_address[:8]}... → {base_addr[:8]}...")
                                    contract_address = base_addr
                                created = dex_data.get("pairCreatedAt", 0)
                                if created:
                                    token_age_hours = (time.time() * 1000 - created) / (1000 * 3600)
                                # Update ticker from DexScreener if different
                                base_token = dex_data.get("baseToken", {})
                                if base_token.get("symbol"):
                                    ticker = base_token["symbol"].upper()
                                logger.info(f"✅ DexScreener: Liq=${_safe_get(dex_data, 'liquidity', 'usd') or 0:,.0f}, "
                                           f"Age={token_age_hours:.1f}h")
            except Exception as e:
                logger.warning(f"⚠️ DexScreener failed: {e}")
            
            # 2. Chain-specific data fetching
            if chain_type == "solana":
                # Birdeye (Solana only)
                logger.info(f"🐦 Fetching Birdeye for address: {contract_address}")
                try:
                    raw = await birdeye_client.get_token_overview(contract_address)
                    if raw:
                        birdeye_data = birdeye_client.extract_scores_from_overview(raw)
                        birdeye_data["raw"] = raw
                        logger.info(f"✅ Birdeye: MC=${birdeye_data.get('market_cap', 0):,.0f}, "
                                   f"Holders={birdeye_data.get('holder_count', 0)}, "
                                   f"Vol=${birdeye_data.get('volume_24h', 0):,.0f}")
                except Exception as e:
                    logger.warning(f"⚠️ Birdeye failed: {e}")
                
                # Solana RPC  
                logger.info(f"🔗 Fetching Solana RPC for: {contract_address}")
                try:
                    token_info = await solana_scraper.get_token_info(contract_address) or {}
                    holder_data = await solana_scraper.get_top_holders(contract_address) or {}
                    # Ensure consistent address usage - use the resolved mint from token_info if available
                    resolved_mint = token_info.get("mint")
                    if resolved_mint and resolved_mint != contract_address:
                        logger.info(f"🔧 Solana RPC resolved mint: {contract_address[:8]}... → {resolved_mint[:8]}...")
                        contract_address = resolved_mint
                    logger.info(f"✅ Solana RPC: mint_renounced={token_info.get('is_mint_renounced')}")
                except Exception as e:
                    logger.warning(f"⚠️ Solana RPC failed: {e}")
                    
            elif chain_type == "evm":
                # EVM-specific data
                logger.info(f"🔗 Fetching EVM data...")
                try:
                    # Detect specific EVM chain from DexScreener data
                    chain_id = dex_data.get("chainId", "base") if dex_data else "base"
                    token_info = await evm_scraper.get_token_info(contract_address, chain_id) or {}
                    token_info["chain"] = chain_id
                    token_info["is_evm"] = True
                    logger.info(f"✅ EVM data: chain={chain_id}")
                except Exception as e:
                    logger.warning(f"⚠️ EVM scraper failed: {e}")
        
        # Use DexScreener-derived market fields as fallback only for EVM chains.
        # For Solana we keep Birdeye empty when Birdeye is unavailable so the source is explicit.
        if chain_type == "evm" and not birdeye_data and dex_data:
            # Extract what we can from DexScreener
            birdeye_data = {
                "market_cap": dex_data.get("marketCap", 0) or dex_data.get("fdv", 0),
                "volume_24h": dex_data.get("volume", {}).get("h24", 0),
                "liquidity": _safe_get(dex_data, "liquidity", "usd") or 0,
                "holder_count": 0,  # Not available from DexScreener
                "price_change_24h": _safe_get(dex_data, "priceChange", "h24") or 0,
            }
            logger.info(f"📊 Using DexScreener data: MC=${birdeye_data.get('market_cap', 0):,.0f}")

        # ══════════════════════════════════════════════════════════════════════
        # STEP 2: Calculate Component Scores
        # ══════════════════════════════════════════════════════════════════════
        
        # Build price changes dict
        price_changes = {
            "1h": birdeye_data.get("price_change_1h") or _safe_get(dex_data, "priceChange", "h1") or 0,
            "6h": _safe_get(dex_data, "priceChange", "h6") or 0,
            "24h": birdeye_data.get("price_change_24h") or _safe_get(dex_data, "priceChange", "h24") or 0,
        }
        
        logger.info(f"📈 Price: 1h={price_changes['1h']:.1f}%, 6h={price_changes['6h']:.1f}%, 24h={price_changes['24h']:.1f}%")
        
        # === TECHNICAL (65%) ===
        momentum_result = tech_analyzer.calculate_momentum_score(price_changes)
        volume_result = tech_analyzer.calculate_volume_score(birdeye_data)
        trade_result = tech_analyzer.calculate_trade_pressure_score(birdeye_data)
        liquidity_result = liquidity_analyzer.calculate_liquidity_score(birdeye_data, holder_data)
        
        logger.info(f"   Momentum: {momentum_result['momentum_score']}/100 ({momentum_result.get('trend')})")
        logger.info(f"   Volume: {volume_result['volume_score']}/100 (Buy%={volume_result.get('buy_volume_pct', 50):.0f}%)")
        logger.info(f"   Trade: {trade_result['trade_pressure_score']}/100 (B/S={trade_result.get('buy_sell_ratio', 1):.2f})")
        logger.info(f"   Liquidity: {liquidity_result['liquidity_score']}/100")
        
        # === ON-CHAIN (35%) ===
        holder_count = birdeye_data.get("holder_count") or holder_data.get("total_holders") or 0
        holder_score = _calc_holder_score(holder_count)
        security_result = _calc_security_score(token_info, token_age_hours)
        top10_pct = holder_data.get("top10_pct") or 50
        whale_score = _calc_whale_score(top10_pct)
        
        logger.info(f"   Holder: {holder_score}/100 ({holder_count:,})")
        logger.info(f"   Security: {security_result['security_score']}/100")
        logger.info(f"   Whale: {whale_score}/100 (top10={top10_pct:.1f}%)")

        # ══════════════════════════════════════════════════════════════════════
        # STEP 3: Calculate Overall Score (65/35/0)
        # ══════════════════════════════════════════════════════════════════════
        
        liq_usd = birdeye_data.get("liquidity") or _safe_get(dex_data, "liquidity", "usd") or 0
        
        overall = _calc_overall_v31(
            momentum=momentum_result.get("momentum_score", 50),
            volume=volume_result.get("volume_score", 50),
            trade=trade_result.get("trade_pressure_score", 50),
            liquidity=liquidity_result.get("liquidity_score", 50),
            holder=holder_score,
            security=security_result.get("security_score", 50),
            whale=whale_score,
            token_info=token_info,
            holder_data=holder_data,
            liq_usd=liq_usd,
            age_hours=token_age_hours,
            holder_count=holder_count
        )
        
        logger.info(f"📊 Overall: {overall['score']}/100 ({overall['risk']}) "
                   f"[raw={overall['raw']}, cap={overall.get('cap')}]")

        # ══════════════════════════════════════════════════════════════════════
        # STEP 4: Build Flags
        # ══════════════════════════════════════════════════════════════════════
        
        red_flags = []
        green_flags = []
        
        # Override reasons → RED FLAGS (CRITICAL FIX!)
        if overall.get("overrides"):
            red_flags.extend(overall["overrides"])
        
        # Technical signals
        trend = momentum_result.get("trend")
        if trend == "DUMP_AFTER_PUMP":
            red_flags.append("🔴 Dump after pump pattern")
        elif trend == "STRONG_BEARISH":
            red_flags.append("🔴 Strong bearish trend")
        elif trend == "STRONG_BULLISH":
            green_flags.append("🟢 Strong bullish trend")
        
        # Volume signals
        buy_pct = volume_result.get("buy_volume_pct", 50)
        if buy_pct > 65:
            green_flags.append(f"🟢 Buy pressure dominant ({buy_pct:.0f}%)")
        elif buy_pct < 35:
            red_flags.append(f"🔴 Heavy sell pressure ({100-buy_pct:.0f}%)")
        
        # Liquidity signals
        if liq_usd > 500000:
            green_flags.append(f"🟢 Good liquidity (${liq_usd:,.0f})")
        elif liq_usd < 50000 and liq_usd > 0:
            red_flags.append(f"🔴 Low liquidity (${liq_usd:,.0f})")
        
        # Security signals
        if token_info.get("is_mint_renounced") or token_info.get("mint_authority") is None:
            green_flags.append("🟢 Mint authority renounced ✓")
        
        # ══════════════════════════════════════════════════════════════════════
        # STEP 5: Run OpenGradient AI Inference
        # ══════════════════════════════════════════════════════════════════════
        
        # Prepare context for AI
        on_chain_context = {
            "holders": holder_data,
            "token_info": token_info,
            "liquidity_usd": liq_usd,
            "smart_money": {} # Disable for now to save tokens
        }
        
        scores_context = {
            "overall_score": overall["score"],
            "social_score": 50,
            "bot_score": 50,
            "holder_score": holder_score
        }
        
        # We don't have tweets since Social is disabled
        empty_tweets = []
        
        logger.info(f"🧠 Running OpenGradient AI Inference...")
        ai_result = await opengradient_analyzer.analyze_coin(
            ticker=ticker,
            tweets_summary=empty_tweets,
            on_chain_data=on_chain_context,
            scoring_result=scores_context
        )
        
        ai_trust_score = None
        ai_inference_success = False
        tx_hash = None
        verify_url = None
        verdict = ""
        
        if ai_result.get("ai_result") and ai_result.get("used_opengradient"):
            ai_data = ai_result["ai_result"]
            
            # Chỉ dùng AI score nếu THẬT SỰ thành công (không phải default)
            if not ai_data.get("verdict", "").startswith("Phân tích AI thất bại"):
                ai_trust_score = ai_data.get("ai_trust_score")
                ai_inference_success = True
                tx_hash = ai_result.get("tx_hash") or ai_result.get("transaction_hash")
                if isinstance(tx_hash, str) and tx_hash.lower() == "external":
                    tx_hash = None
                verify_url = f"https://explorer.opengradient.ai/tx/{tx_hash}" if tx_hash else None
                verdict = ai_data.get("verdict", "")
                logger.info(f"✅ AI inference SUCCESS: trust={ai_trust_score}, tx={tx_hash}")
                
                # Merge AI flags
                red_flags.extend(ai_data.get("red_flags", []))
                green_flags.extend(ai_data.get("green_flags", []))
                
                # Dedupe again
                red_flags = list(dict.fromkeys(red_flags))
                green_flags = list(dict.fromkeys(green_flags))
            else:
                logger.warning("⚠️ AI returned default fallback - ignoring")

        # Always get the algorithmic score first
        score = overall["score"]
        
        # Generate verdict từ ALGORITHMIC score (không dùng AI)
        if not ai_inference_success or not verdict:
            if score >= 70:
                verdict = f"${ticker}: Strong technical metrics. Relatively safe for meme coin."
            elif score >= 50:
                verdict = f"${ticker}: Mixed signals. DYOR before investing."
            elif score >= 35:
                verdict = f"${ticker}: Multiple risk factors. High risk."
            else:
                verdict = f"${ticker}: Extreme caution. Very high risk of loss."
                
            if overall.get("cap"):
                verdict += f" (Capped at {overall['cap']} due to risk factors)"

        # ══════════════════════════════════════════════════════════════════════
        # STEP 6: Save to DB
        # ══════════════════════════════════════════════════════════════════════
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        report = AnalysisReport(
            coin_id=coin.id,
            overall_score=score,
            risk_level=overall["risk"],
            social_score=50,
            bot_score=50,
            sentiment_score=50,
            holder_score=holder_score,
            verdict=verdict,
            red_flags=json.dumps(red_flags),
            green_flags=json.dumps(green_flags),
            tweet_count_analyzed=0,
            analysis_duration_ms=duration_ms,
        )
        db.add(report)
        db.commit()
        
        coin.last_checked_at = datetime.utcnow()
        db.commit()

        # ══════════════════════════════════════════════════════════════════════
        # STEP 7: Build Response
        # ══════════════════════════════════════════════════════════════════════
        
        scores = ScoreBreakdown(
            social_score=50,
            bot_score=50,
            sentiment_score=50,
            holder_score=holder_score,
            security_score=security_result.get("security_score"),
            momentum_score=momentum_result.get("momentum_score"),
            volume_score=volume_result.get("volume_score"),
            liquidity_score=liquidity_result.get("liquidity_score"),
            ai_trust_score=None,
        )
        
        breakdown = {
            "technical": {
                "total": round(overall["tech_total"], 1),
                "weight": "65%",
                "components": {
                    "momentum": {"score": momentum_result.get("momentum_score", 50), "weight": "25%", 
                                "trend": momentum_result.get("trend"), "details": momentum_result.get("details", [])},
                    "volume": {"score": volume_result.get("volume_score", 50), "weight": "18%",
                              "buy_pct": volume_result.get("buy_volume_pct", 50), "details": volume_result.get("details", [])},
                    "trade_pressure": {"score": trade_result.get("trade_pressure_score", 50), "weight": "12%",
                                       "buy_sell_ratio": trade_result.get("buy_sell_ratio", 1.0), "details": trade_result.get("details", [])},
                    "liquidity": {"score": liquidity_result.get("liquidity_score", 50), "weight": "10%",
                                 "liquidity_usd": liq_usd, "details": liquidity_result.get("details", [])}
                }
            },
            "onchain": {
                "total": round(overall["onchain_total"], 1),
                "weight": "35%",
                "components": {
                    "holder": {"score": holder_score, "weight": "15%", "holder_count": holder_count},
                    "security": {"score": security_result.get("security_score", 50), "weight": "12%",
                                "mint_renounced": token_info.get("is_mint_renounced", False),
                                "token_age_hours": round(token_age_hours, 1)},
                    "whale_risk": {"score": whale_score, "weight": "8%", "top10_pct": top10_pct}
                }
            },
            "social": {
                "total": 0,
                "weight": "0%",
                "status": "DISABLED",
                "note": "Twitter scraping will be enabled in future version"
            }
        }
        
        data_sources = {
            "chain": chain_type,
            "birdeye": {"used": bool(birdeye_data), "holder_count": birdeye_data.get("holder_count"),
                       "volume_24h": birdeye_data.get("volume_24h")},
            "dexscreener": {"used": bool(dex_data), "liquidity_usd": _safe_get(dex_data, "liquidity", "usd")},
            "solana_rpc": {"used": bool(token_info) and chain_type == "solana"},
            "evm_scan": {"used": bool(token_info) and chain_type == "evm", "chain": token_info.get("chain", "base") if chain_type == "evm" else None},
            "twitter": {"status": "DISABLED"}
        }
        
        response = {
            "ticker": ticker,
            "contract_address": contract_address,
            "overall_score": score,
            "trust_score": ai_trust_score if ai_inference_success and ai_trust_score is not None else score,
            "risk_level": overall["risk"],
            "scores": scores,
            "breakdown": breakdown,
            "technical_indicators": {
                "trend": momentum_result.get("trend"),
                "divergence": momentum_result.get("divergence"),
                "buy_sell_ratio": trade_result.get("buy_sell_ratio"),
            },
            "signals": {"bullish": green_flags, "bearish": red_flags, "neutral": []},
            "verdict": verdict,
            "red_flags": red_flags,
            "green_flags": green_flags,
            "tx_hash": tx_hash,
            "verify_url": verify_url,
            "tweets_analyzed": 0,
            "analyzed_at": datetime.utcnow(),
            "cached": False,
            "opengradient_used": ai_result.get("used_opengradient", False),
            "model_used": ai_result.get("model_cid") or ai_result.get("model_used"),
            "algorithm_notes": [
                f"Scoring: v3.1 (Technical 65% / On-Chain 35%) - Chain: {chain_type.upper()}",
                "Social: DISABLED",
                f"AI: {'✅ Verified by OpenGradient' if ai_inference_success else '⚠️ Algorithmic Only'}",
                f"Data: DexScreener={'✅' if dex_data else '❌'}"
            ],
            "data_sources": data_sources,
        }
        
        # Cache
        await cache.set(CacheKeys.full_analysis(contract_address or ticker), response, CacheTTL.FULL_ANALYSIS)
        
        logger.info(f"✅ Done {ticker} in {duration_ms}ms | score={score} risk={overall['risk']}")
        
        return AnalyzeResponse(**response)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"❌ Failed: {ticker}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _safe_get(d: dict, *keys):
    """Safely get nested dict value."""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _calc_holder_score(count: int) -> int:
    """Holder score (15% weight)."""
    if not count: return 50
    if count > 100000: return 100
    if count >= 50000: return 90
    if count >= 20000: return 80
    if count >= 10000: return 70
    if count >= 5000: return 60
    if count >= 2000: return 50
    if count >= 1000: return 35
    if count >= 500: return 25
    if count >= 100: return 15
    return 5


def _calc_security_score(info: dict, age_hours: float) -> dict:
    """Security score (12% weight)."""
    if not info:
        return {"security_score": 50}
    
    # Mint (40%)
    mint = 100 if info.get("mint_authority") is None or info.get("is_mint_renounced") else 0
    
    # Freeze (25%)
    freeze = 100 if info.get("freeze_authority") is None else 20
    
    # Age (35%)
    if age_hours >= 2160: age = 100
    elif age_hours >= 720: age = 85
    elif age_hours >= 336: age = 70
    elif age_hours >= 168: age = 55
    elif age_hours >= 72: age = 40
    elif age_hours >= 24: age = 25
    else: age = 10
    
    return {"security_score": int(mint * 0.40 + freeze * 0.25 + age * 0.35)}


def _calc_whale_score(top10: float) -> int:
    """Whale risk score (8% weight)."""
    if not top10: return 50
    if top10 < 20: return 100
    if top10 <= 30: return 85
    if top10 <= 40: return 70
    if top10 <= 50: return 55
    if top10 <= 60: return 40
    if top10 <= 70: return 25
    if top10 <= 80: return 15
    return 5


def _calc_overall_v31(
    momentum: int, volume: int, trade: int, liquidity: int,
    holder: int, security: int, whale: int,
    token_info: dict, holder_data: dict, liq_usd: float, age_hours: float, holder_count: int
) -> dict:
    """
    Overall score v3.1: Technical 65% / On-Chain 35% / Social 0%
    """
    # Technical (65%)
    tech = momentum * 0.25 + volume * 0.18 + trade * 0.12 + liquidity * 0.10
    
    # On-Chain (35%)
    onchain = holder * 0.15 + security * 0.12 + whale * 0.08
    
    raw = tech + onchain
    
    # Override rules
    cap = 100
    overrides = []
    
    is_established = holder_count > 10000 or age_hours > 720
    is_large = liq_usd > 1000000 or holder_count > 50000
    
    # Mint authority
    if token_info.get("mint_authority") and not token_info.get("is_mint_renounced"):
        if is_large:
            cap = min(cap, 70)
            overrides.append("🟡 Mint authority ACTIVE (large cap) → max 70")
        elif is_established:
            cap = min(cap, 55)
            overrides.append("🟡 Mint authority ACTIVE (established) → max 55")
        else:
            cap = min(cap, 35)
            overrides.append("🔴 Mint authority ACTIVE → max 35")
    
    # Low liquidity
    if liq_usd < 10000:
        if is_established:
            cap = min(cap, 50)
            overrides.append("🟡 Liquidity < $10K (established) → max 50")
        else:
            cap = min(cap, 30)
            overrides.append("🔴 Liquidity < $10K → max 30")
    
    # Whale concentration
    top10 = holder_data.get("top10_pct", 0)
    if top10 > 80:
        if is_large:
            cap = min(cap, 65)
            overrides.append("🟡 Top 10 > 80% (large cap) → max 65")
        elif is_established:
            cap = min(cap, 50)
            overrides.append("🟡 Top 10 > 80% (established) → max 50")
        else:
            cap = min(cap, 30)
            overrides.append("🔴 Top 10 hold > 80% → max 30")
    
    # Low holders
    if holder_count and holder_count < 100:
        cap = min(cap, 35)
        overrides.append("🔴 Holders < 100 → max 35")
    elif holder_count and holder_count < 500 and not is_established:
        cap = min(cap, 45)
        overrides.append("🟡 Holders < 500 → max 45")
    
    # New token
    if age_hours < 24:
        if holder_count > 1000:
            cap = min(cap, 55)
            overrides.append("🟡 Token < 24h but trending → max 55")
        else:
            cap = min(cap, 40)
            overrides.append("🔴 Token < 24h old → max 40")
    
    # Freeze authority
    if token_info.get("freeze_authority"):
        if is_large:
            cap = min(cap, 75)
        else:
            cap = min(cap, 50)
            overrides.append("🟡 Freeze authority active → max 50")
    
    final = max(0, min(100, min(int(raw), cap)))
    
    # Risk level
    if final >= 80: risk = "MOON"
    elif final >= 65: risk = "LOW"
    elif final >= 50: risk = "MEDIUM"
    elif final >= 35: risk = "HIGH"
    elif final >= 20: risk = "EXTREME"
    else: risk = "AVOID"
    
    return {
        "score": final,
        "risk": risk,
        "raw": int(raw),
        "cap": cap if cap < 100 else None,
        "overrides": overrides,
        "tech_total": tech,
        "onchain_total": onchain
    }


# ══════════════════════════════════════════════════════════════════════════════
# OTHER ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/coin/{ticker}/history", response_model=HistoryResponse)
async def get_history(ticker: str, limit: int = 10, db: Session = Depends(get_db)):
    coin = db.query(Coin).filter(Coin.symbol == ticker.upper()).first()
    if not coin:
        raise HTTPException(404, "Coin not found")
    
    reports = db.query(AnalysisReport).filter(
        AnalysisReport.coin_id == coin.id
    ).order_by(AnalysisReport.created_at.desc()).limit(limit).all()
    
    return HistoryResponse(
        ticker=ticker.upper(),
        reports=[ReportSummary(
            id=r.id, overall_score=r.overall_score, risk_level=r.risk_level,
            verdict=r.verdict, tx_hash=r.tx_hash, analyzed_at=r.created_at
        ) for r in reports]
    )


@router.get("/api/trending", response_model=TrendingResponse)
async def get_trending(db: Session = Depends(get_db)):
    cutoff = datetime.utcnow() - timedelta(hours=24)
    results = db.query(
        Coin.symbol,
        sql_func.count(AnalysisReport.id).label("cnt"),
        sql_func.avg(AnalysisReport.overall_score).label("avg")
    ).join(AnalysisReport).filter(
        AnalysisReport.created_at > cutoff
    ).group_by(Coin.symbol).order_by(
        sql_func.count(AnalysisReport.id).desc()
    ).limit(20).all()
    
    return TrendingResponse(trending=[
        TrendingItem(ticker=r.symbol, checks_24h=r.cnt, avg_score=round(r.avg, 1) if r.avg else None)
        for r in results
    ])


@router.post("/api/admin/clear-cache")
async def clear_cache():
    await cache.clear()
    return {"status": "ok"}
