# api/routes.py — v3.1 Technical Focus (Twitter DISABLED)
# Weights: Technical 65% / On-Chain 35% / Social 0%

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func as sql_func
from datetime import datetime, timedelta
from contextvars import ContextVar
from typing import Optional, Callable, Awaitable
import asyncio
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
from scrapers.rugcheck_client import get_rugcheck_client
from scrapers.solana_tracker_client import get_solana_tracker_client
from analysis.technical_analyzer import TechnicalAnalyzer
from analysis.liquidity_analyzer import LiquidityAnalyzer
from analysis.opengradient import OpenGradientAnalyzer
from analysis.wash_detector import detect_wash_trade_score
from analysis.volatility import realized_volatility, volume_trend_delta
from analysis.cross_validator import cross_validate_scores
from utils.cache import cache, CacheKeys, CacheTTL
import scoring_loader
import os as _os


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

_ANALYZE_STREAM_META_CB: ContextVar[Optional[Callable[[dict], Awaitable[None]]]] = ContextVar(
    "_ANALYZE_STREAM_META_CB", default=None
)
_SKIP_OG_AI: ContextVar[bool] = ContextVar("_SKIP_OG_AI", default=False)

# Services
solana_scraper = SolanaScraper()
evm_scraper = create_evm_scraper()
birdeye_client = create_birdeye_client()
tech_analyzer = TechnicalAnalyzer()
liquidity_analyzer = LiquidityAnalyzer()
opengradient_analyzer = OpenGradientAnalyzer()

# OPT-3: Persistent HTTP client with connection pooling (reused across requests)
_shared_http_client: httpx.AsyncClient | None = None

def _get_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
    return _shared_http_client


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


@router.get("/api/trending/dex/solana")
async def get_trending_dex_solana(limit: int = 8):
    cache_key = "trending:dex:solana"
    cached = await cache.get(cache_key)
    if cached:
        return cached

    limit = max(1, min(int(limit or 8), 20))

    boosts_url = "https://api.dexscreener.com/token-boosts/top/v1"
    try:
        async with httpx.AsyncClient() as client:
            boosts_resp = await client.get(boosts_url, timeout=10.0)
            boosts_resp.raise_for_status()
            boosts = boosts_resp.json()
    except Exception as e:
        logger.error(f"DexScreener trending boosts failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch DexScreener trending")

    sol = [b for b in boosts if isinstance(b, dict) and b.get("chainId") == "solana" and b.get("tokenAddress")]
    sol = sol[:limit]
    addresses = [b.get("tokenAddress") for b in sol]

    tokens_url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(addresses)}"
    try:
        async with httpx.AsyncClient() as client:
            tokens_resp = await client.get(tokens_url, timeout=10.0)
            tokens_resp.raise_for_status()
            pairs = tokens_resp.json().get("pairs", [])
    except Exception as e:
        logger.error(f"DexScreener tokens lookup failed: {e}")
        pairs = []

    results = []
    for b in sol:
        addr = b.get("tokenAddress")
        addr_l = addr.lower()
        token_pairs = [p for p in pairs if (p.get("chainId") == "solana") and (p.get("baseToken") or {}).get("address", "").lower() == addr_l]
        best = None
        for p in token_pairs:
            liq = _safe_get(p, "liquidity", "usd") or 0
            try:
                liq = float(liq)
            except Exception:
                liq = 0
            if best is None or liq > (float(_safe_get(best, "liquidity", "usd") or 0) if isinstance(best, dict) else 0):
                best = p

        base = (best or {}).get("baseToken") if isinstance(best, dict) else {}
        symbol = (base.get("symbol") or "").upper()
        name = base.get("name")
        url = (best or {}).get("url") or b.get("url")
        liq_usd = _safe_get(best or {}, "liquidity", "usd")

        if symbol:
            results.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "address": addr,
                    "url": url,
                    "liquidity_usd": liq_usd,
                    "boost": b.get("totalAmount") or b.get("amount"),
                }
            )

    payload = {"trending": results}
    await cache.set(cache_key, payload, ttl=60 * 60)
    return payload


@router.get("/api/ohlcv")
async def get_ohlcv(address: str, interval: str = "15m", limit: int = 200):
    if _detect_chain(address) != "solana":
        raise HTTPException(status_code=400, detail="Only Solana addresses are supported")

    limit = max(1, min(int(limit or 200), 500))
    raw_items = await birdeye_client.get_ohlcv(address, interval=interval, limit=limit) or []

    candles = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        t = item.get("unixTime") or item.get("time") or item.get("t")
        if t is None:
            continue
        try:
            t = int(t)
        except Exception:
            continue
        if t > 1_000_000_000_000:
            t = int(t / 1000)

        def _num(*keys):
            for k in keys:
                if k in item:
                    try:
                        return float(item[k])
                    except Exception:
                        return None
            return None

        o = _num("open", "o")
        h = _num("high", "h")
        l = _num("low", "l")
        c = _num("close", "c")
        if o is None or h is None or l is None or c is None:
            continue

        candles.append({"time": t, "open": o, "high": h, "low": l, "close": c})

    return {"candles": candles}


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
    llm_model = getattr(request, "llm_model", None)
    start_time = time.time()

    def _normalize_llm_model(value: str | None) -> str | None:
        if value is None:
            return None
        model = str(value).strip()
        return model or None

    # Official supported models from https://docs.opengradient.ai/developers/x402/
    allowed_llm_models = {
        "openai/gpt-5",
        "openai/gpt-5-2",
        "openai/gpt-5-mini",
        "openai/gpt-4.1-2025-04-14",
        "openai/gpt-4o",
        "openai/o4-mini",
        "anthropic/claude-opus-4-6",
        "anthropic/claude-opus-4-5",
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-sonnet-4-5",
        "anthropic/claude-haiku-4-5",
        "google/gemini-3-pro",
        "google/gemini-3-flash",
        "google/gemini-2.5-pro",
        "google/gemini-2.5-flash",
        "google/gemini-2.5-flash-lite",
        "google/gemini-2.0-flash",
        "x-ai/grok-4",
        "x-ai/grok-4-fast",
        "x-ai/grok-4.1-fast",
        "x-ai/grok-4-1-fast-non-reasoning",
    }

    normalized_model = _normalize_llm_model(llm_model)
    if normalized_model and normalized_model not in allowed_llm_models:
        logger.warning(f"Invalid llm_model requested: {normalized_model}. Falling back to default.")
        normalized_model = None
    effective_model = normalized_model or "anthropic/claude-sonnet-4-6"
    
    # Detect chain type
    chain_type = _detect_chain(contract_address or ticker)
    logger.info(f"📊 ===== ANALYSIS START: '{ticker}' =====")
    logger.info(f"📍 Chain: {chain_type}, ticker: '{ticker}', contract: '{contract_address}', refresh: {force_refresh}")

    # Auto-detect / normalize contract-address inputs.
    # If the user types/pastes an address into the ticker field, we still want to resolve a real symbol
    # even when request.contract_address is already provided (frontend sends both).
    if len(ticker) > 30 and " " not in ticker:
        if not contract_address:
            contract_address = ticker
        chain_type = _detect_chain(contract_address)
        logger.info(f"📍 Detected contract-like input: {contract_address[:8]}... (chain: {chain_type})")

        # Try to resolve ticker from address (helps UI show symbol instead of raw address)
        if chain_type == "solana":
            try:
                resolved = await solana_scraper.get_token_symbol(contract_address)
                ticker = resolved.upper() if resolved else ticker[:8].upper()
            except Exception:
                ticker = ticker[:8].upper()
        elif chain_type == "evm":
            ticker = ticker[:8].upper()
        else:
            ticker = ticker[:8].upper()
    else:
        ticker = ticker.upper()

        # Search DexScreener for contract if not provided
        if not contract_address:
            logger.info(f"🔍 Searching DexScreener for {ticker}...")
            try:
                client = _get_http_client()  # OPT-3: reuse persistent client
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
                                chain_type = "solana" if target_chain == "solana" else "evm"
                                logger.info(f"✅ Resolved {ticker} → {contract_address[:8]}... ({target_chain})")
                                break
            except Exception as e:
                logger.error(f"❌ DexScreener search failed: {e}")

    if not contract_address:
        raise HTTPException(status_code=400, detail="Unable to resolve contract address")

    # OPT-1: Full analysis cache check — skip all API calls on cache hit
    _full_cache_key = CacheKeys.full_analysis(contract_address, effective_model)
    if not force_refresh:
        _cached_result = await cache.get(_full_cache_key)
        if _cached_result:
            logger.info(f"⚡ Cache HIT full analysis: {contract_address[:8]}...")
            _cached_result = dict(_cached_result)
            _cached_result["cached"] = True
            return AnalyzeResponse(**_cached_result)

    try:
        # ══════════════════════════════════════════════════════════════════════
        # STEP 1: Fetch Data from APIs (Chain-specific)
        # ══════════════════════════════════════════════════════════════════════
        
        birdeye_data = {}
        dex_data = {}
        token_info = {}
        holder_data = {}
        token_age_hours = None
        birdeye_used = False
        
        if contract_address:
            # 1. DexScreener (Universal - works for all chains)
            # OPT-4: Check DexScreener cache first
            _dex_cache_key = CacheKeys.dex_data(contract_address)
            _dex_from_cache = False
            if not force_refresh:
                _dex_cached = await cache.get(_dex_cache_key)
                if _dex_cached:
                    dex_data = _dex_cached.get("dex_data", {})
                    contract_address = _dex_cached.get("contract_address", contract_address)
                    ticker = _dex_cached.get("ticker", ticker)
                    token_age_hours = _dex_cached.get("token_age_hours")
                    _dex_from_cache = True
                    logger.info(f"⚡ DexScreener cache HIT: {contract_address[:8]}...")

            if not _dex_from_cache:
                logger.info(f"🌐 Fetching DexScreener...")
                try:
                    # OPT-3: reuse persistent client
                    client = _get_http_client()
                    requested_addr = contract_address
                    requested_addr_l = requested_addr.lower() if isinstance(requested_addr, str) else ""

                    # Token endpoint first
                    resp = await client.get(
                        f"https://api.dexscreener.com/latest/dex/tokens/{contract_address}",
                        timeout=10.0,
                    )
                    pairs = []
                    if resp.status_code == 200:
                        pairs = resp.json().get("pairs", []) or []

                    # Prefer pairs where the input matches either baseToken or quoteToken.
                    chain_pairs = []
                    if requested_addr_l:
                        chain_pairs = [
                            p
                            for p in pairs
                            if (
                                p.get("baseToken", {}).get("address", "").lower() == requested_addr_l
                                or p.get("quoteToken", {}).get("address", "").lower() == requested_addr_l
                            )
                        ]

                    # If not found, user likely pasted a *pair address*.
                    if not chain_pairs:
                        pair_candidates = []
                        try:
                            if chain_type == "solana":
                                pair_resp = await client.get(
                                    f"https://api.dexscreener.com/latest/dex/pairs/solana/{contract_address}",
                                    timeout=10.0,
                                )
                                if pair_resp.status_code == 200:
                                    pair_candidates = pair_resp.json().get("pairs", []) or []
                            elif chain_type == "evm":
                                # Try a small set of common EVM chains.
                                for ch in ("base", "ethereum", "bsc"):
                                    pair_resp = await client.get(
                                        f"https://api.dexscreener.com/latest/dex/pairs/{ch}/{contract_address}",
                                        timeout=10.0,
                                    )
                                    if pair_resp.status_code == 200:
                                        pair_candidates = pair_resp.json().get("pairs", []) or []
                                        if pair_candidates:
                                            break
                        except Exception:
                            pair_candidates = []

                        if pair_candidates:
                            chain_pairs = pair_candidates

                    if chain_pairs:
                        dex_data = max(
                            chain_pairs,
                            key=lambda x: (x.get("liquidity") or {}).get("usd", 0) or 0,
                        )

                        base_addr = _safe_get(dex_data, "baseToken", "address")
                        quote_addr = _safe_get(dex_data, "quoteToken", "address")

                        # Determine which token in the pair corresponds to the requested contract.
                        # If not matched, assume the user pasted a *pair address* and default to baseToken.
                        token_side = "base"
                        if requested_addr_l and quote_addr and str(quote_addr).lower() == requested_addr_l:
                            token_side = "quote"
                        elif requested_addr_l and base_addr and str(base_addr).lower() == requested_addr_l:
                            token_side = "base"

                        token_from_pair = (
                            dex_data.get("quoteToken") if token_side == "quote" else dex_data.get("baseToken")
                        )

                        # Canonicalize contract to the matched token address when available.
                        token_addr = token_from_pair.get("address") if isinstance(token_from_pair, dict) else None
                        if token_addr and contract_address and str(token_addr).lower() != contract_address.lower():
                            logger.info(f"🔧 Normalized token address: {contract_address[:8]}... → {str(token_addr)[:8]}...")
                            contract_address = token_addr

                        created = dex_data.get("pairCreatedAt", 0)
                        if created:
                            token_age_hours = (time.time() * 1000 - created) / (1000 * 3600)

                        # Update ticker from DexScreener if different
                        if isinstance(token_from_pair, dict) and token_from_pair.get("symbol"):
                            ticker = str(token_from_pair["symbol"]).upper()

                        age_str = f"{token_age_hours:.1f}h" if token_age_hours is not None else "unknown"
                        logger.info(
                            f"✅ DexScreener: Liq=${_safe_get(dex_data, 'liquidity', 'usd') or 0:,.0f}, "
                            f"Age={age_str}"
                        )

                        # OPT-4: Save DexScreener data to cache
                        await cache.set(_dex_cache_key, {
                            "dex_data": dex_data,
                            "contract_address": contract_address,
                            "ticker": ticker,
                            "token_age_hours": token_age_hours,
                        }, CacheTTL.DEX_DATA)
                except Exception as e:
                    logger.warning(f"⚠️ DexScreener failed: {e}")
            
            # 2. Chain-specific data fetching
            if chain_type == "solana":
                # Solana RPC FIRST - to resolve the mint address (Birdeye needs Base58)
                logger.info(f"🔗 Fetching Solana RPC for: {contract_address}")
                try:
                    token_info = await solana_scraper.get_token_info(contract_address)
                    if not token_info:
                        raise HTTPException(
                            status_code=400,
                            detail="Only token contract (mint) addresses are supported. Wallet addresses cannot be analyzed.",
                        )
                    # Resolve mint address - Birdeye requires Base58 format
                    resolved_mint = token_info.get("mint")
                    if resolved_mint and resolved_mint != contract_address:
                        logger.info(f"🔧 Solana RPC resolved mint: {contract_address[:8]}... → {resolved_mint[:8]}...")
                        contract_address = resolved_mint
                    logger.info(f"✅ Solana RPC: mint_renounced={token_info.get('is_mint_renounced')}")
                except HTTPException:
                    raise
                except Exception as e:
                    logger.warning(f"⚠️ Solana RPC failed: {e}")
                    raise HTTPException(
                        status_code=502,
                        detail="Solana RPC validation failed. Unable to confirm this is a token mint address.",
                    )

                # OPT-2: Fetch holders + BirdEye in PARALLEL (both need resolved mint)
                _mint = contract_address

                # OPT-6: Pass prefetched token_info to avoid duplicate RPC call
                _prefetched_ti = token_info

                async def _fetch_holders_task():
                    try:
                        return await solana_scraper.get_top_holders(_mint, prefetched_token_info=_prefetched_ti) or {}
                    except Exception as _e:
                        logger.warning(f"⚠️ Holders fetch failed: {_e}")
                        return {}

                async def _fetch_birdeye_task():
                    try:
                        _bk = CacheKeys.birdeye_overview(_mint)
                        _raw = None
                        if not force_refresh:
                            _raw = await cache.get(_bk)
                        if not _raw:
                            _raw = await birdeye_client.get_token_overview(_mint)
                            if _raw:
                                await cache.set(_bk, _raw, CacheTTL.BIRDEYE_OVERVIEW)
                        return _raw
                    except Exception as _e:
                        logger.warning(f"⚠️ Birdeye fetch failed: {_e}")
                        return None

                logger.info(f"⚡ Fetching holders + BirdEye in parallel...")
                _holders_result, _birdeye_raw = await asyncio.gather(
                    _fetch_holders_task(),
                    _fetch_birdeye_task(),
                )

                holder_data = _holders_result
                if _birdeye_raw:
                    birdeye_data = birdeye_client.extract_scores_from_overview(_birdeye_raw)
                    birdeye_data["raw"] = _birdeye_raw
                    birdeye_used = True
                    if isinstance(holder_data, dict) and birdeye_data.get("holder_count") and not holder_data.get("total_holders"):
                        holder_data["total_holders"] = birdeye_data.get("holder_count")
                    mc = birdeye_data.get('market_cap') or 0
                    holders = birdeye_data.get('holder_count') or 0
                    vol = birdeye_data.get('volume_24h') or 0
                    logger.info(f"✅ Birdeye: MC=${mc:,.0f}, Holders={holders:,}, Vol=${vol:,.0f}")
                else:
                    logger.warning(f"⚠️ Birdeye unavailable")

                # Fallback: token age from token_info (it also contains DexScreener pairCreatedAt)
                if token_age_hours is None and isinstance(token_info, dict):
                    created2 = token_info.get("pairCreatedAt")
                    if created2:
                        try:
                            token_age_hours = (time.time() * 1000 - float(created2)) / (1000 * 3600)
                        except Exception:
                            pass
                    
            elif chain_type == "evm":
                # EVM-specific data
                logger.info(f"🔗 Fetching EVM data...")
                try:
                    # Detect specific EVM chain from DexScreener data
                    chain_id = dex_data.get("chainId", "base") if dex_data else "base"
                    token_info = await evm_scraper.get_token_info(contract_address, chain_id)
                    if token_info:
                        token_info["chain"] = chain_id
                        token_info["is_evm"] = True
                    if not token_info and not dex_data:
                        raise HTTPException(
                            status_code=400,
                            detail="Only token contract addresses are supported. Wallet addresses cannot be analyzed.",
                        )
                    logger.info(f"✅ EVM data: chain={chain_id}")
                except HTTPException:
                    raise
                except Exception as e:
                    token_info = {}
                    logger.warning(f"⚠️ EVM scraper failed: {e}")
        
        # Fallback market fields from DexScreener when Birdeye is unavailable.
        # This improves scoring for tokens where Birdeye is down / rate-limited.
        if not birdeye_used and dex_data:
            birdeye_data = {
                "market_cap": dex_data.get("marketCap", 0) or dex_data.get("fdv", 0),
                "volume_24h": _safe_get(dex_data, "volume", "h24") or 0,
                "liquidity": _safe_get(dex_data, "liquidity", "usd") or 0,
                "holder_count": 0,
                "price_change_1h": _safe_get(dex_data, "priceChange", "h1") or 0,
                "price_change_24h": _safe_get(dex_data, "priceChange", "h24") or 0,
                "buys_24h": _safe_get(dex_data, "txns", "h24", "buys") or 0,
                "sells_24h": _safe_get(dex_data, "txns", "h24", "sells") or 0,
            }
            logger.info(f"📊 Using DexScreener fallback market data for scoring: MC=${birdeye_data.get('market_cap', 0):,.0f}")

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
        raw_top10 = holder_data.get("top10_pct") if isinstance(holder_data, dict) else None
        top10_pct = float(raw_top10) if isinstance(raw_top10, (int, float)) else None
        whale_score = _calc_whale_score(top10_pct) if top10_pct is not None else 50
        
        logger.info(f"   Holder: {holder_score}/100 ({holder_count:,})")
        logger.info(f"   Security: {security_result['security_score']}/100")
        top10_str = f"{top10_pct:.1f}%" if isinstance(top10_pct, (int, float)) else "unknown"
        logger.info(f"   Whale: {whale_score}/100 (top10={top10_str})")

        # ══════════════════════════════════════════════════════════════════════
        # STEP 3: Calculate Overall Score (65/35/0)
        # ══════════════════════════════════════════════════════════════════════
        
        liq_usd = birdeye_data.get("liquidity") or _safe_get(dex_data, "liquidity", "usd") or 0

        market_cap = (
            birdeye_data.get("market_cap")
            or dex_data.get("marketCap")
            or dex_data.get("fdv")
            or token_info.get("marketCap")
            or 0
        )

        if isinstance(token_info, dict) and token_age_hours is not None:
            token_info["age_hours"] = token_age_hours
        
        # ═══════════════════════════════════════════════════════════════════
        # v4.0 — Parallel enrichment: RugCheck + OHLCV + recent txs
        # ═══════════════════════════════════════════════════════════════════
        # Fires three independent fetches in one gather(). Each is optional;
        # any failure degrades gracefully to None and scoring substitutes
        # sensible defaults (see _calc_overall_v40 + YAML config).
        rugcheck_report: dict | None = None
        ohlcv_candles: list | None = None
        recent_txs: list | None = None
        tracker_report: dict | None = None

        async def _fetch_rugcheck():
            if chain_type != "solana" or not contract_address:
                return None
            try:
                rc = get_rugcheck_client(cache=cache)
                return await rc.get_report(contract_address)
            except Exception as e:
                logger.info(f"RugCheck skipped ({e})")
                return None

        async def _fetch_ohlcv():
            if chain_type != "solana" or not contract_address:
                return None
            try:
                # 24 × 1h candles → 24h realized vol window
                return await birdeye_client.get_ohlcv(
                    contract_address, interval="1H", limit=24
                )
            except Exception as e:
                logger.info(f"OHLCV skipped ({e})")
                return None

        async def _fetch_recent_txs():
            if (
                chain_type != "solana"
                or not contract_address
                or _os.getenv("ENABLE_WASH_DETECTION", "true").lower() == "false"
            ):
                return None
            try:
                return await birdeye_client.get_token_txs(contract_address, limit=50)
            except Exception as e:
                logger.info(f"Recent txs skipped ({e})")
                return None

        async def _fetch_solana_tracker():
            if chain_type != "solana" or not contract_address:
                return None
            try:
                st = get_solana_tracker_client(cache=cache)
                if not st.enabled:
                    return None
                return await st.get_token(contract_address)
            except Exception as e:
                logger.info(f"Solana Tracker skipped ({e})")
                return None

        rugcheck_report, ohlcv_candles, recent_txs, tracker_report = await asyncio.gather(
            _fetch_rugcheck(),
            _fetch_ohlcv(),
            _fetch_recent_txs(),
            _fetch_solana_tracker(),
        )

        if rugcheck_report:
            logger.info(
                f"🛡️ RugCheck: risk={rugcheck_report.get('risk_score_1_10')}, "
                f"insider={rugcheck_report.get('insider_pct'):.1f}%, "
                f"lp_locked={rugcheck_report.get('lp_locked_pct'):.0f}%"
            )

        # --- Realized volatility + volume trend from OHLCV ---
        realized_vol_value: float | None = None
        volume_trend_value: float | None = None
        if ohlcv_candles:
            realized_vol_value = realized_volatility(ohlcv_candles)
            volume_trend_value = volume_trend_delta(ohlcv_candles)
            logger.info(
                f"📉 Volatility: {realized_vol_value if realized_vol_value is None else f'{realized_vol_value:.2f}'} "
                f"| Volume trend: {volume_trend_value}%"
            )

        # --- Wash-trade heuristic from recent txs ---
        wash_result = None
        wash_score_value: float | None = None
        if recent_txs:
            wash_result = detect_wash_trade_score(recent_txs)
            if not wash_result.get("insufficient_data"):
                wash_score_value = wash_result["score"]
                logger.info(
                    f"🧼 Wash score: {wash_score_value:.2f} "
                    f"(flip={wash_result['components']['rapid_flip']:.2f}, "
                    f"unique={wash_result['components']['low_unique_ratio']:.2f}, "
                    f"conc={wash_result['components']['volume_concentration']:.2f})"
                )

        # Compute real volume/mcap ratio so v4.0 Market.Vol/MCap sub-score
        # reflects the raw data (wash-trade ceiling at 5x) rather than the
        # legacy blended `volume_score`.
        _vol24 = birdeye_data.get("volume_24h") or _safe_get(dex_data, "volume", "h24") or 0
        _mc = float(market_cap) if isinstance(market_cap, (int, float)) else 0.0
        vm_ratio = (_vol24 / _mc) if (_mc > 0 and _vol24 > 0) else None

        overall = _calc_overall_v40(
            momentum=momentum_result.get("momentum_score", 50),
            volume=volume_result.get("volume_score", 50),
            trade=trade_result.get("trade_pressure_score", 50),
            liquidity=liquidity_result.get("liquidity_score", 50),
            holder=holder_score,
            security_legacy=security_result.get("security_score", 50),
            whale=whale_score,
            market_cap=market_cap,
            token_info=token_info,
            holder_data=holder_data,
            liq_usd=liq_usd,
            age_hours=token_age_hours,
            holder_count=holder_count,
            rugcheck=rugcheck_report,
            wash_trade_score=wash_score_value,
            realized_vol=realized_vol_value,
            volume_trend_delta=volume_trend_value,
            vol_mcap_ratio=vm_ratio,
        )

        # --- Cross-validate signals across sources ---
        cross_validation = cross_validate_scores(
            birdeye={"top10_pct": top10_pct, "holder_count": holder_count} if top10_pct is not None else None,
            rugcheck=rugcheck_report,
            tracker=tracker_report,
        )
        if tracker_report:
            logger.info(
                f"📊 Solana Tracker: risk={tracker_report.get('risk_score')}, "
                f"top10={tracker_report.get('top10_pct')}"
            )
        if cross_validation["conflicting"]:
            logger.info(
                f"⚠️ Cross-validation conflicts: {cross_validation['conflicts']}"
            )
        
        logger.info(f"📊 Overall: {overall['score']}/100 ({overall['risk']}) "
                   f"[raw={overall['raw']}, cap={overall.get('cap')}]")

        # ══════════════════════════════════════════════════════════════════════
        # STEP 4: Build Flags
        # ══════════════════════════════════════════════════════════════════════

        # Compute rug_score composite early — red/green flags reference it
        # (previously computed later near the LLM context; moved up to avoid
        # an undefined-variable error when expert flags fire).
        rug_composite = _calc_rug_score_composite(rugcheck_report, holder_data, token_info)

        red_flags = []
        green_flags = []

        # Override reasons → RED FLAGS (CRITICAL FIX!)
        if overall.get("overrides"):
            red_flags.extend(overall["overrides"])
        
        # Technical signals
        trend = momentum_result.get("trend")
        if trend == "DUMP_AFTER_PUMP":
            red_flags.append("Dump-after-pump pattern — fast spikes often crash quickly")
        elif trend == "STRONG_BEARISH":
            red_flags.append("Strong bearish trend — momentum is down")
        elif trend == "STRONG_BULLISH":
            green_flags.append("Strong bullish trend — momentum is up")
        
        # Volume signals
        buy_pct = volume_result.get("buy_volume_pct", 50)
        if buy_pct > 65:
            green_flags.append(f"Buy pressure dominant ({buy_pct:.0f}%) — more buys than sells recently")
        elif buy_pct < 35:
            red_flags.append(f"Heavy sell pressure ({100-buy_pct:.0f}%) — more sells than buys recently")
        
        # Liquidity signals
        if liq_usd > 500000:
            green_flags.append(f"Good liquidity (${liq_usd:,.0f}) — easier to trade without big price swings")
        elif liq_usd < 50000 and liq_usd > 0:
            red_flags.append(f"Low liquidity (${liq_usd:,.0f}) — small trades can move price a lot")
        
        # Security signals
        if token_info.get("is_mint_renounced") or token_info.get("mint_authority") is None:
            green_flags.append("Mint authority renounced — supply cannot be inflated by printing more tokens")

        # ─────────────────────────────────────────────────────────────────
        # v4.0 EXPERT FLAGS — surface the specific data-driven signals so the
        # LLM and UI don't have to infer them from raw numbers.
        # ─────────────────────────────────────────────────────────────────

        vol_24h_flag = birdeye_data.get("volume_24h") or _safe_get(dex_data, "volume", "h24") or 0
        mc_flag = float(market_cap) if isinstance(market_cap, (int, float)) else 0.0

        # 1) Vol/MCap ratio — the single strongest wash-trading tell
        if mc_flag > 0 and vol_24h_flag > 0:
            vm_ratio = vol_24h_flag / mc_flag
            if vm_ratio >= 5.0:
                red_flags.append(
                    f"Volume/MCap = {vm_ratio:.1f}x (${vol_24h_flag:,.0f} vol on ${mc_flag:,.0f} mcap) — "
                    f"statistically impossible for organic trading, almost certainly wash"
                )
            elif vm_ratio >= 2.0:
                red_flags.append(
                    f"Volume/MCap = {vm_ratio:.1f}x — manipulation territory, real volume/mcap rarely exceeds 1x"
                )
            elif 0.1 <= vm_ratio <= 0.5:
                green_flags.append(
                    f"Volume/MCap = {vm_ratio:.2f}x — healthy organic trading range"
                )

        # 2) Realized volatility (annualized)
        if realized_vol_value is not None:
            if realized_vol_value > 5.0:
                red_flags.append(
                    f"Realized volatility {realized_vol_value:.1f}× annualized — "
                    f"BTC is ~0.8×, normal crypto 1.5–3×. This is pump-dump territory"
                )
            elif realized_vol_value < 0.8:
                green_flags.append(
                    f"Realized volatility {realized_vol_value:.2f}× — low-risk price action"
                )

        # 3) Wash trade score from tx-pattern detector (separate from vol/mcap)
        if wash_score_value is not None:
            if wash_score_value > 0.5:
                comp = (wash_result or {}).get("components", {})
                red_flags.append(
                    f"Wash-trade pattern detected (score {wash_score_value:.2f}) — "
                    f"rapid buy-sell flips {comp.get('rapid_flip', 0):.0%}, "
                    f"concentration {comp.get('volume_concentration', 0):.0%}"
                )
            elif wash_score_value < 0.2:
                green_flags.append(
                    f"Clean trade pattern (wash score {wash_score_value:.2f}) — unique wallets, no rapid flips"
                )

        # 4) Rug composite (independent risk index 0–100, higher = worse)
        rc_pct = rug_composite["rug_score"]
        if rc_pct >= 70:
            red_flags.append(
                f"Rug score {rc_pct:.0f}/100 — composite risk index in DANGER zone"
            )
        elif rc_pct <= 20:
            green_flags.append(
                f"Rug score {rc_pct:.0f}/100 — composite index in SAFE zone"
            )

        # 5) RugCheck specific fields (when available)
        if rugcheck_report:
            ins = float(rugcheck_report.get("insider_pct") or 0)
            bun = float(rugcheck_report.get("bundler_pct") or 0)
            snp = float(rugcheck_report.get("sniper_pct") or 0)
            lp_lk = float(rugcheck_report.get("lp_locked_pct") or 0)

            if ins >= 10:
                red_flags.append(
                    f"Insider wallets hold {ins:.1f}% of supply (RugCheck) — coordinated dump risk"
                )
            elif ins < 3 and (ins + bun + snp) < 10:
                green_flags.append(
                    f"Low insider/bundler/sniper exposure ({ins:.1f}% / {bun:.1f}% / {snp:.1f}%)"
                )

            if lp_lk >= 80:
                burned_note = " (burned)" if rugcheck_report.get("lp_burned") else " (locked)"
                green_flags.append(f"LP {lp_lk:.0f}% secured{burned_note} via RugCheck — dev can't pull liquidity")
            elif lp_lk < 20:
                red_flags.append(
                    f"LP only {lp_lk:.0f}% locked — dev can rug-pull liquidity at any moment"
                )

            if not rugcheck_report.get("freeze_authority_active"):
                green_flags.append("Freeze authority disabled — dev cannot freeze user wallets")

        # 6) Holder + age context (already have override caps, but surface plain-language)
        if holder_count and holder_count < 500:
            # Already in caps; add human framing only when really bad
            if holder_count < 100 and not any("holders" in f.lower() for f in red_flags):
                red_flags.append(
                    f"Only {holder_count} holders — one coordinated dump crashes this to zero"
                )

        # 7) Cross-source conflict
        if cross_validation["conflicting"]:
            red_flags.append(
                f"Signals disagree across sources ({', '.join(cross_validation['sources_used'])}) — "
                f"data reliability is LOW"
            )

        # Dedupe while preserving order
        red_flags = list(dict.fromkeys(red_flags))
        green_flags = list(dict.fromkeys(green_flags))

        # ══════════════════════════════════════════════════════════════════════
        # STEP 5: Run OpenGradient AI Inference
        # ══════════════════════════════════════════════════════════════════════
        
        # Prepare context for AI
        volume_24h = birdeye_data.get("volume_24h") or _safe_get(dex_data, "volume", "h24") or 0
        buy_sell_ratio = trade_result.get("buy_sell_ratio")

        dex_chain_id = dex_data.get("chainId")
        dex_pair_address = dex_data.get("pairAddress")
        dex_url = dex_data.get("url")
        if not dex_url and dex_chain_id and dex_pair_address:
            dex_url = f"https://dexscreener.com/{dex_chain_id}/{dex_pair_address}"

        chart_url = None
        if dex_url:
            sep = "&" if "?" in dex_url else "?"
            chart_url = f"{dex_url}{sep}embed=1&theme=dark"

        dex_base = dex_data.get("baseToken") if isinstance(dex_data, dict) else None
        dex_quote = dex_data.get("quoteToken") if isinstance(dex_data, dict) else None

        dex_token = None
        try:
            ca_l = contract_address.lower() if isinstance(contract_address, str) else ""
            if ca_l and isinstance(dex_base, dict) and str(dex_base.get("address") or "").lower() == ca_l:
                dex_token = dex_base
            elif ca_l and isinstance(dex_quote, dict) and str(dex_quote.get("address") or "").lower() == ca_l:
                dex_token = dex_quote
        except Exception:
            dex_token = None
        if not isinstance(dex_token, dict):
            dex_token = dex_base if isinstance(dex_base, dict) else None

        token_name = dex_token.get("name") if isinstance(dex_token, dict) else None
        token_symbol = dex_token.get("symbol") if isinstance(dex_token, dict) else None
        if not token_symbol and isinstance(token_info, dict):
            token_symbol = token_info.get("symbol")
        token_logo = None
        if isinstance(birdeye_data.get("raw"), dict):
            token_logo = (
                birdeye_data["raw"].get("logoURI")
                or birdeye_data["raw"].get("logo")
                or birdeye_data["raw"].get("logo_url")
            )
        token_info_block = dex_data.get("info") if isinstance(dex_data, dict) else None
        if not token_logo and isinstance(token_info_block, dict):
            token_logo = (
                token_info_block.get("imageUrl")
                or token_info_block.get("image")
                or token_info_block.get("logoURI")
            )
        token_links = token_info_block if isinstance(token_info_block, dict) else {}

        market_data = {
            "price": birdeye_data.get("price") or dex_data.get("priceUsd"),
            "market_cap": market_cap,
            "liquidity_usd": liq_usd,
            "volume_24h": volume_24h,
            "holders": holder_count,
            "price_change_1h": price_changes.get("1h"),
            "price_change_24h": price_changes.get("24h"),
            "buy_volume_pct": volume_result.get("buy_volume_pct"),
            "buy_sell_ratio": buy_sell_ratio,
            "token_age_hours": round(token_age_hours, 2) if token_age_hours is not None else None,
            "token": {
                "name": token_name,
                "symbol": token_symbol,
                "address": contract_address,
                "logo": token_logo,
                "links": token_links,
                "dex_id": dex_data.get("dexId"),
            },
            "chart_url": chart_url,
        }

        # rug_composite already computed earlier in STEP 4 (flag building).
        on_chain_context = {
            "holders": holder_data,
            "token_info": token_info,
            "liquidity_usd": liq_usd,
            "market_data": market_data,
            "smart_money": {},            # disabled
            # v4.0 — enrich LLM context without changing prompt shape:
            # the existing analyze_coin() dumps on_chain_data as JSON, so extra
            # keys flow through as additional signal without code changes.
            "rugcheck": {
                "risk_score": rugcheck_report.get("risk_score_1_10"),
                "insider_pct": rugcheck_report.get("insider_pct"),
                "bundler_pct": rugcheck_report.get("bundler_pct"),
                "sniper_pct": rugcheck_report.get("sniper_pct"),
                "lp_locked_pct": rugcheck_report.get("lp_locked_pct"),
                "mint_authority_active": rugcheck_report.get("mint_authority_active"),
                "freeze_authority_active": rugcheck_report.get("freeze_authority_active"),
                "top_holders_pct": rugcheck_report.get("top_holders_pct"),
                "risks": rugcheck_report.get("risks", [])[:6],
            } if rugcheck_report else None,
            "v4_breakdown": {
                "security_total": overall.get("security_total"),
                "market_total": overall.get("market_total"),
                "momentum_total": overall.get("momentum_total"),
                "rug_score": rug_composite["rug_score"],
                "rug_score_breakdown": rug_composite["breakdown"],
                "overrides_triggered": overall.get("overrides", [])[:12],
                "wash_trade_score": wash_score_value,
                "wash_trade_breakdown": (wash_result or {}).get("components"),
                "realized_volatility": round(realized_vol_value, 3) if realized_vol_value is not None else None,
                "volume_trend_delta_pct": volume_trend_value,
                "confidence": cross_validation["confidence"],
                "conflicting_signals": cross_validation["conflicting"],
                # Full sub-score breakdown so the LLM can cite exact numbers
                # that match what the UI displays.
                "components": overall.get("components"),
            },
            # Raw signal numbers — lets the LLM reason about ratios (vol/mcap
            # = 16.7x) and flag wash-trade territory without re-deriving them.
            "raw_signals": {
                "vol_mcap_ratio": round(vm_ratio, 3) if vm_ratio is not None else None,
                "liquidity_mcap_pct": (round(liq_usd / market_cap * 100, 2)
                                       if isinstance(market_cap, (int, float)) and market_cap > 0 else None),
                "top10_pct": top10_pct,
                "buy_volume_pct": volume_result.get("buy_volume_pct"),
                "buy_sell_ratio": trade_result.get("buy_sell_ratio"),
                "holder_count": holder_count,
                "age_hours": token_age_hours,
            },
            # Pre-computed analyst flags — saves the LLM from re-deriving the
            # same conclusions. It should still lead with the 1–3 strongest.
            "red_flags_detected": red_flags,
            "green_flags_detected": green_flags,
        }

        scores_context = {
            "overall_score": overall["score"],
            "holder_score": holder_score,
            "security_score": security_result.get("security_score"),
            "liquidity_score": liquidity_result.get("liquidity_score"),
            "momentum_score": momentum_result.get("momentum_score"),
            "volume_score": volume_result.get("volume_score"),
            "trade_pressure_score": trade_result.get("trade_pressure_score"),
        }
        
        # We don't have tweets since Social is disabled
        empty_tweets = []
        
        # OPT-5: Skip AI when called from chat (chat runs its own LLM call)
        skip_ai = _SKIP_OG_AI.get()
        if skip_ai:
            logger.info(f"⚡ Skipping OpenGradient AI (chat mode)")
            ai_result = {"ai_result": None, "used_opengradient": False}
        else:
            logger.info(f"🧠 Running OpenGradient AI Inference...")

        stream_meta_cb = _ANALYZE_STREAM_META_CB.get()
        if skip_ai:
            pass
        elif stream_meta_cb:
            ai_result = None
            async for evt in opengradient_analyzer.analyze_coin_stream(
                ticker=ticker,
                tweets_summary=empty_tweets,
                on_chain_data=on_chain_context,
                scoring_result=scores_context,
                llm_model=effective_model,
            ):
                if evt.get("type") == "meta":
                    await stream_meta_cb(evt)
                elif evt.get("type") == "done":
                    ai_result = evt.get("result")
            ai_result = ai_result or {}
        else:
            ai_result = await opengradient_analyzer.analyze_coin(
                ticker=ticker,
                tweets_summary=empty_tweets,
                on_chain_data=on_chain_context,
                scoring_result=scores_context,
                llm_model=effective_model,
            )

        # OpenGradient inference result
        # Graceful degrade: if the TEE/AI call fails (network, registry, timeout,
        # rate-limit), we still return the full algorithmic audit (scores,
        # breakdown_v4, rug_score, red/green flags). The user sees everything
        # except the LLM narrative — which is better than a blanket 502.
        ai_error = ai_result.get("error") if isinstance(ai_result, dict) else None
        if ai_error:
            logger.warning(f"⚠️ OpenGradient error (degrading gracefully): {ai_error}")
            ai_result = {
                "ai_result": {
                    "verdict": (
                        f"**Algorithmic analysis only.** The AI verdict layer is temporarily "
                        f"unavailable (`{ai_error[:120]}`).\n\n"
                        f"The score, breakdown, and red/green flags below were computed from "
                        f"on-chain data (Birdeye, RugCheck, DexScreener). Use them as your "
                        f"primary signal. Re-run the audit in a minute for the LLM narrative."
                    ),
                },
                "used_opengradient": False,
                "error": ai_error,
            }
        ai_trust_score = None
        ai_inference_success = False
        tx_hash = None
        payment_hash = None
        settlement_mode = None
        verification = None
        verify_url = None
        verdict = ""
        verdict_action = None

        # When the AI path failed (graceful-degrade above), surface the fallback
        # verdict text so the UI doesn't render an empty card.
        if ai_error and isinstance(ai_result.get("ai_result"), dict):
            verdict = ai_result["ai_result"].get("verdict", "") or ""

        if ai_result.get("ai_result") and ai_result.get("used_opengradient"):
            ai_data = ai_result["ai_result"]

            # Only use AI score if SUCCESSFUL (not default fallback)
            if not ai_data.get("verdict", "").startswith("AI Analysis failed"):
                ai_trust_score = ai_data.get("ai_trust_score")
                ai_inference_success = True
                payment_hash = ai_result.get("payment_hash")
                settlement_mode = ai_result.get("settlement_mode")
                verification = ai_result.get("verification")
                tx_hash = ai_result.get("tx_hash") or ai_result.get("transaction_hash")
                if isinstance(tx_hash, str) and tx_hash.lower() == "external":
                    tx_hash = None
                verify_url = f"https://explorer.opengradient.ai/tx/{tx_hash}" if tx_hash else None
                verdict = ai_data.get("verdict", "")
                verdict_action = ai_data.get("verdict_action")
                logger.info(f"✅ AI inference SUCCESS: trust={ai_trust_score}, action={verdict_action}, tx={tx_hash}")
                
                # Merge AI flags
                red_flags.extend(ai_data.get("red_flags", []))
                green_flags.extend(ai_data.get("green_flags", []))
                
                # Dedupe again
                red_flags = list(dict.fromkeys(red_flags))
                green_flags = list(dict.fromkeys(green_flags))
            else:
                logger.warning("⚠️ AI returned default fallback - ignoring")

        require_x402 = bool(getattr(opengradient_analyzer, "REQUIRE_X402_TX", False))
        if require_x402 and ai_inference_success and not tx_hash:
            logger.warning(
                "⚠️ OpenGradient x402 verification did not produce a verifiable tx_hash; continuing without verification"
            )

        # Always get the algorithmic score first
        score = overall["score"]
        
        # Generate verdict from ALGORITHMIC score (don't use AI fallback)
        if not ai_inference_success or not verdict:
            top_green = green_flags[0] if green_flags else None
            top_red = red_flags[0] if red_flags else None

            if score >= 70:
                verdict_action = verdict_action or "APE"
                verdict = (
                    f"${ticker}: Lower risk than average for a meme coin based on current on-chain + market signals. "
                    f"Positive signal: {top_green or 'none detected'}. "
                    f"Main risk: {top_red or 'none detected'}. "
                    "Not financial advice. Meme coins are high-volatility; you can lose 100%."
                )
            elif score >= 50:
                verdict_action = verdict_action or "WATCH"
                verdict = (
                    f"${ticker}: Mixed signals. Positive signal: {top_green or 'none detected'}. "
                    f"Main risk: {top_red or 'none detected'}. "
                    "If you are new, consider waiting and researching more. "
                    "Not financial advice. Meme coins are high-volatility; you can lose 100%."
                )
            elif score >= 35:
                verdict_action = verdict_action or "SKIP"
                verdict = (
                    f"${ticker}: High risk. Main risk: {top_red or 'multiple risk factors'}. "
                    "If you are new, avoid or only risk money you can afford to lose. "
                    "Not financial advice. Meme coins are high-volatility; you can lose 100%."
                )
            else:
                verdict_action = verdict_action or "RUN"
                verdict = (
                    f"${ticker}: Extreme caution. Main risk: {top_red or 'severe risk factors'}. "
                    "For beginners, the safest action is to avoid. "
                    "Not financial advice. Meme coins are high-volatility; you can lose 100%."
                )

            if overall.get("cap"):
                verdict += f" (Score was capped at {overall['cap']}/100 due to risk factors.)"

        # ══════════════════════════════════════════════════════════════════════
        # STEP 6: Save to DB
        # ══════════════════════════════════════════════════════════════════════
        
        duration_ms = int((time.time() - start_time) * 1000)

        coin = crud.get_or_create_coin(db, ticker, contract_address)
        
        report = AnalysisReport(
            coin_id=coin.id,
            overall_score=score,
            risk_level=overall["risk"],
            social_score=None,
            bot_score=None,
            sentiment_score=None,
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
            holder_score=holder_score,
            security_score=security_result.get("security_score"),
            momentum_score=momentum_result.get("momentum_score"),
            volume_score=volume_result.get("volume_score"),
            liquidity_score=liquidity_result.get("liquidity_score"),
            ai_trust_score=ai_trust_score if ai_inference_success and ai_trust_score is not None else None,
        )
        
        scale_factor = 1.0
        if overall.get("raw") and isinstance(overall.get("score"), int) and overall["score"] < overall["raw"]:
            try:
                scale_factor = float(overall["score"]) / float(overall["raw"]) if overall["raw"] else 1.0
            except Exception:
                scale_factor = 1.0

        tech_contrib_raw = float(overall.get("tech_total") or 0)
        onchain_contrib_raw = float(overall.get("onchain_total") or 0)
        tech_contrib = round(tech_contrib_raw * scale_factor, 1)
        onchain_contrib = round(onchain_contrib_raw * scale_factor, 1)

        tech_total_100 = round((tech_contrib / 0.65) if tech_contrib else 0, 1)
        onchain_total_100 = round((onchain_contrib / 0.35) if onchain_contrib else 0, 1)

        breakdown = {
            "technical": {
                "total": tech_total_100,
                "weight": "65%",
                "contribution": tech_contrib,
                "contribution_raw": round(tech_contrib_raw, 1),
                "components": {
                    "momentum": {"score": momentum_result.get("momentum_score", 50), "weight": "20%", 
                                "trend": momentum_result.get("trend"), "details": momentum_result.get("details", [])},
                    "volume": {"score": volume_result.get("volume_score", 50), "weight": "15%",
                              "buy_pct": volume_result.get("buy_volume_pct", 50), "details": volume_result.get("details", [])},
                    "trade_pressure": {"score": trade_result.get("trade_pressure_score", 50), "weight": "10%",
                                       "buy_sell_ratio": trade_result.get("buy_sell_ratio", 1.0), "details": trade_result.get("details", [])},
                    "liquidity": {"score": liquidity_result.get("liquidity_score", 50), "weight": "20%",
                                 "liquidity_usd": liq_usd, "details": liquidity_result.get("details", [])}
                }
            },
            "onchain": {
                "total": onchain_total_100,
                "weight": "35%",
                "contribution": onchain_contrib,
                "contribution_raw": round(onchain_contrib_raw, 1),
                "components": {
                    "holder": {"score": holder_score, "weight": "12%", "holder_count": holder_count},
                    "security": {"score": security_result.get("security_score", 50), "weight": "13%",
                                "mint_renounced": token_info.get("is_mint_renounced", False),
                                "token_age_hours": round(token_age_hours, 1) if token_age_hours is not None else None},
                    "whale_risk": {"score": whale_score, "weight": "10%", "top10_pct": top10_pct}
                }
            }
        }

        # ─── v4.0 BREAKDOWN (emitted alongside legacy shape) ─────────────────
        # UI consumers that understand v4.0 can read this; legacy UI ignores it
        # and keeps reading `breakdown` unchanged.
        v4_weights = overall.get("weights") or {"security": 0.40, "market": 0.35, "momentum": 0.25}
        v4_components = overall.get("components") or {}
        sec_c = v4_components.get("security") or {}
        mkt_c = v4_components.get("market") or {}
        mom_c = v4_components.get("momentum") or {}
        sec_total = float(overall.get("security_total") or 0)
        mkt_total = float(overall.get("market_total") or 0)
        mom_total = float(overall.get("momentum_total") or 0)
        breakdown_v4 = {
            "scoring_version": "4.0",
            "weights": {
                "security": f"{v4_weights['security']*100:.0f}%",
                "market": f"{v4_weights['market']*100:.0f}%",
                "momentum": f"{v4_weights['momentum']*100:.0f}%",
            },
            "security": {
                "total": round(sec_total, 1),
                "weight": f"{v4_weights['security']*100:.0f}%",
                "contribution": round(sec_total * v4_weights["security"], 1),
                "components": {
                    "mint_authority": {"score": sec_c.get("mint", 0), "weight": "25%",
                                       "active": sec_c.get("mint_active", False)},
                    "freeze_authority": {"score": sec_c.get("freeze", 0), "weight": "25%",
                                         "active": sec_c.get("freeze_active", False)},
                    "lp_locked": {"score": sec_c.get("lp_locked", 0), "weight": "25%",
                                  "locked_pct": sec_c.get("lp_locked_pct", 0)},
                    "insider_clusters": {"score": sec_c.get("insider", 0), "weight": "25%",
                                         "insider_pct": sec_c.get("insider_pct", 0),
                                         "bundler_pct": sec_c.get("bundler_pct", 0)},
                },
            },
            "market": {
                "total": round(mkt_total, 1),
                "weight": f"{v4_weights['market']*100:.0f}%",
                "contribution": round(mkt_total * v4_weights["market"], 1),
                "components": {
                    "liquidity": {"score": mkt_c.get("liquidity", 0), "weight": "29%",
                                  "liquidity_usd": liq_usd},
                    "volume_mcap_ratio": {"score": mkt_c.get("volume_mcap", 0), "weight": "23%"},
                    "buy_sell_pressure": {"score": mkt_c.get("buy_sell", 0), "weight": "20%",
                                          "buy_sell_ratio": trade_result.get("buy_sell_ratio")},
                    "top10_holder_pct": {"score": mkt_c.get("top10", 0), "weight": "14%",
                                         "top10_pct": top10_pct},
                    "wash_trade": {"score": mkt_c.get("wash", 60), "weight": "14%"},
                },
            },
            "momentum": {
                "total": round(mom_total, 1),
                "weight": f"{v4_weights['momentum']*100:.0f}%",
                "contribution": round(mom_total * v4_weights["momentum"], 1),
                "components": {
                    "price_action": {"score": mom_c.get("price_action", 50), "weight": "40%",
                                     "trend": momentum_result.get("trend")},
                    "realized_volatility": {"score": mom_c.get("volatility", 50), "weight": "32%"},
                    "volume_trend": {"score": mom_c.get("volume_trend", 50), "weight": "28%"},
                },
            },
            "rug_score": rug_composite["rug_score"],
            "rug_score_breakdown": rug_composite["breakdown"],
            "overrides_applied": overall.get("overrides", []),
        }

        data_sources = {
            "chain": chain_type,
            "birdeye": {"used": bool(birdeye_used), "holder_count": birdeye_data.get("holder_count"),
                       "volume_24h": birdeye_data.get("volume_24h")},
            "dexscreener": {"used": bool(dex_data), "liquidity_usd": _safe_get(dex_data, "liquidity", "usd")},
            "solana_rpc": {"used": bool(token_info) and chain_type == "solana"},
            "evm_scan": {"used": bool(token_info) and chain_type == "evm", "chain": token_info.get("chain", "base") if chain_type == "evm" else None},
            "rugcheck": {
                "used": bool(rugcheck_report),
                "risk_score_1_10": (rugcheck_report or {}).get("risk_score_1_10"),
                "insider_pct": (rugcheck_report or {}).get("insider_pct"),
                "lp_locked_pct": (rugcheck_report or {}).get("lp_locked_pct"),
            },
            "solana_tracker": {
                "used": bool(tracker_report),
                "risk_score": (tracker_report or {}).get("risk_score"),
                "top10_pct": (tracker_report or {}).get("top10_pct"),
            },
            "twitter": {"status": "DISABLED"}
        }
        
        response = {
            "ticker": ticker,
            "contract_address": contract_address,
            "overall_score": score,
            "trust_score": score,
            "risk_level": overall["risk"],
            "scores": scores,
            "breakdown": breakdown,
            "breakdown_v4": breakdown_v4,
            "market_data": market_data,
            "technical_indicators": {
                "trend": momentum_result.get("trend"),
                "divergence": momentum_result.get("divergence"),
                "buy_sell_ratio": trade_result.get("buy_sell_ratio"),
            },
            "signals": {"bullish": green_flags, "bearish": red_flags, "neutral": []},
            "verdict": verdict,
            "verdict_action": verdict_action,
            "red_flags": red_flags,
            "green_flags": green_flags,
            "rug_score": rug_composite["rug_score"],
            "rug_score_breakdown": rug_composite["breakdown"],
            "wash_trade_score": wash_score_value,
            "wash_trade_breakdown": (wash_result or {}).get("components") if wash_result else None,
            "realized_volatility": round(realized_vol_value, 3) if realized_vol_value is not None else None,
            "volume_trend_delta_pct": volume_trend_value,
            "confidence_score": cross_validation["confidence"],
            "conflicting_signals": cross_validation["conflicting"],
            "cross_validation": cross_validation,
            "tx_hash": tx_hash,
            "payment_hash": payment_hash,
            "settlement_mode": settlement_mode,
            "verification": verification,
            "verify_url": verify_url,
            "tweets_analyzed": 0,
            "analyzed_at": datetime.utcnow(),
            "cached": False,
            "opengradient_used": ai_result.get("used_opengradient", False),
            "model_used": ai_result.get("model_cid") or ai_result.get("model_used"),
            "algorithm_notes": [
                f"Scoring: v4.0 (Security 40% / Market 35% / Momentum 25%) - Chain: {chain_type.upper()}",
                "Social: DISABLED",
                f"AI: {'✅ Verified by OpenGradient (TEE + settlement hash)' if ai_inference_success and tx_hash else ('⚠️ OpenGradient inference ok but settlement tx not verifiable yet' if ai_inference_success else '⚠️ Algorithmic Only')}",
                f"Data: DexScreener={'✅' if dex_data else '❌'}"
            ],
            "data_sources": data_sources,
        }
        
        logger.info(f"✅ Done {ticker} in {duration_ms}ms | score={score} risk={overall['risk']}")

        # OPT-1: Save full analysis to cache for subsequent fast hits
        try:
            _cacheable = dict(response)
            _cacheable["analyzed_at"] = jsonable_encoder(_cacheable["analyzed_at"])
            await cache.set(_full_cache_key, _cacheable, CacheTTL.FULL_ANALYSIS)
        except Exception:
            pass

        return AnalyzeResponse(**response)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"❌ Failed: {ticker}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/analyze/stream")
async def analyze_coin_stream(request: AnalyzeRequest, db: Session = Depends(get_db)):
    async def _event_gen():
        queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        meta_sent = False

        async def _meta_cb(meta_evt: dict) -> None:
            nonlocal meta_sent
            meta_sent = True
            await queue.put(
                (
                    "meta",
                    {
                        "tx_hash": meta_evt.get("tx_hash"),
                        "payment_hash": meta_evt.get("payment_hash"),
                        "verification": meta_evt.get("verification"),
                    },
                )
            )

        async def _run() -> None:
            nonlocal meta_sent
            token = _ANALYZE_STREAM_META_CB.set(_meta_cb)
            try:
                res = await analyze_coin(request, db)
                payload = jsonable_encoder(res)
                if (payload.get("tx_hash") or payload.get("payment_hash")) and not meta_sent:
                    await queue.put(
                        (
                            "meta",
                            {
                                "tx_hash": payload.get("tx_hash"),
                                "payment_hash": payload.get("payment_hash"),
                                "verification": payload.get("verification"),
                            },
                        )
                    )
                    meta_sent = True
                await queue.put(("result", payload))
            except HTTPException as e:
                await queue.put(("error", {"detail": e.detail, "status_code": e.status_code}))
            except Exception as e:
                await queue.put(("error", {"detail": str(e), "status_code": 500}))
            finally:
                _ANALYZE_STREAM_META_CB.reset(token)
                await queue.put(("done", {}))

        asyncio.create_task(_run())

        yield "event: open\ndata: {}\n\n"
        while True:
            # Send heartbeat every 10s to keep Railway proxy alive (default 30s timeout)
            try:
                event_name, data = await asyncio.wait_for(queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield f"event: {event_name}\n" + f"data: {json.dumps(data)}\n\n"
            if event_name == "done":
                break

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# CHAT ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    llm_model: Optional[str] = None


class ChatResponse(BaseModel):
    role: str = "assistant"
    content: str
    analysis_data: Optional[dict] = None
    contract_detected: Optional[str] = None


@router.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, db: Session = Depends(get_db)):
    """
    Chat endpoint: auto-detects contracts from user messages,
    runs analysis internally, and returns conversational AI response.
    """
    try:
        from api.chat import detect_contract, build_analysis_context, build_messages

        user_msg = request.message.strip()
        if not user_msg:
            return ChatResponse(content="Hi! Paste a contract address or ask anything about meme coins 🚀")

        llm_model = request.llm_model or "anthropic/claude-sonnet-4-6"
        contract = detect_contract(user_msg)
        analysis_data = None
        analysis_context = None

        if contract:
            logger.info(f"💬 Chat: detected contract {contract[:12]}... in message")
            # OPT-5: Skip OpenGradient AI in analyze_coin — chat runs its own LLM
            _skip_token = _SKIP_OG_AI.set(True)
            try:
                analyze_req = AnalyzeRequest(
                    ticker=contract,
                    contract_address=contract,
                    force_refresh=False,
                    llm_model=llm_model,
                )
                result = await analyze_coin(analyze_req, db)
                analysis_data = jsonable_encoder(result)
                analysis_context = build_analysis_context(analysis_data, query_address=contract)
                logger.info(f"✅ Chat: analysis complete for {contract[:12]}...")
            except HTTPException as e:
                logger.warning(f"⚠️ Chat: analysis failed: {e.detail}")
                analysis_context = f"Analysis failed for contract {contract}: {e.detail}"
            except Exception as e:
                logger.warning(f"⚠️ Chat: analysis error: {e}")
                analysis_context = f"Analysis error for contract {contract}: {str(e)}"
            finally:
                _SKIP_OG_AI.reset(_skip_token)

        # Build messages for LLM
        messages = build_messages(
            user_message=user_msg,
            history=request.history,
            analysis_context=analysis_context,
        )

        # Get AI response
        chat_result = await opengradient_analyzer.chat_completion(
            messages=messages,
            llm_model=llm_model,
        )

        if chat_result.get("error"):
            # Fallback: return analysis data directly without LLM
            if analysis_context:
                content = f"⚠️ AI chat unavailable, but here's the analysis:\n\n{analysis_context}"
            else:
                content = f"⚠️ AI service temporarily unavailable: {chat_result['error']}"
        else:
            content = chat_result.get("content", "Sorry, I couldn't generate a response.")

        return ChatResponse(
            content=content,
            analysis_data=analysis_data,
            contract_detected=contract,
        )
    except Exception as outer_e:
        logger.exception("🔥 CRITICAL: Chat endpoint crashed")
        raise HTTPException(status_code=500, detail=f"Internal chat error: {str(outer_e)}")


@router.post("/api/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, db: Session = Depends(get_db)):
    """Streaming chat via SSE: emits analysis data then streams LLM tokens."""
    from api.chat import detect_contract, build_analysis_context, build_messages

    async def event_gen():
        user_msg = request.message.strip()
        if not user_msg:
            yield f"data: {json.dumps({'type': 'done', 'full_content': 'Hi! Paste a contract address or ask anything about meme coins.'})}\n\n"
            return

        llm_model = request.llm_model or "anthropic/claude-sonnet-4-6"
        contract = detect_contract(user_msg)
        analysis_data = None
        analysis_context = None

        # Phase 1: Analysis (with cache — fast if recently checked)
        # OPT-5: Skip OpenGradient AI — chat streams its own LLM
        if contract:
            yield f"data: {json.dumps({'type': 'phase', 'label': 'Scanning chain data...'})}\n\n"
            _skip_token = _SKIP_OG_AI.set(True)
            try:
                analyze_req = AnalyzeRequest(
                    ticker=contract,
                    contract_address=contract,
                    force_refresh=False,
                    llm_model=llm_model,
                )
                result = await analyze_coin(analyze_req, db)
                analysis_data = jsonable_encoder(result)
                analysis_context = build_analysis_context(analysis_data, query_address=contract)
                yield f"data: {json.dumps({'type': 'analysis', 'data': analysis_data, 'contract': contract})}\n\n"
            except HTTPException as e:
                analysis_context = f"Analysis failed for {contract}: {e.detail}"
            except Exception as e:
                analysis_context = f"Analysis error: {str(e)}"
            finally:
                _SKIP_OG_AI.reset(_skip_token)

        # Phase 2: Stream LLM response
        messages = build_messages(
            user_message=user_msg,
            history=request.history,
            analysis_context=analysis_context,
        )
        yield f"data: {json.dumps({'type': 'phase', 'label': 'Generating response...'})}\n\n"

        full_content = ""
        try:
            async for chunk in opengradient_analyzer.chat_completion_stream(
                messages=messages, llm_model=llm_model
            ):
                if chunk["type"] == "chunk":
                    full_content += chunk["content"]
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk['content']})}\n\n"
                elif chunk["type"] == "error":
                    if analysis_context:
                        full_content = f"⚠️ AI unavailable, but here's the analysis:\n\n{analysis_context}"
                    else:
                        full_content = f"⚠️ AI service temporarily unavailable: {chunk['error']}"
                    yield f"data: {json.dumps({'type': 'fallback', 'content': full_content})}\n\n"
                    break
        except Exception as e:
            full_content = f"⚠️ Stream error: {str(e)}"
            yield f"data: {json.dumps({'type': 'fallback', 'content': full_content})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'full_content': full_content})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


def _calc_security_score(info: dict, age_hours: float | None) -> dict:
    """Security score (12% weight)."""
    if not info:
        return {"security_score": 50}
    
    # Mint (40%)
    mint = 100 if info.get("mint_authority") is None or info.get("is_mint_renounced") else 0
    
    # Freeze (25%)
    freeze = 100 if info.get("freeze_authority") is None else 20
    
    # Age (35%)
    if age_hours is None:
        age = 55
    elif age_hours >= 2160:
        age = 100
    elif age_hours >= 720:
        age = 85
    elif age_hours >= 336:
        age = 70
    elif age_hours >= 168:
        age = 55
    elif age_hours >= 72:
        age = 40
    elif age_hours >= 24:
        age = 25
    else:
        age = 10
    
    return {"security_score": int(mint * 0.40 + freeze * 0.25 + age * 0.35)}


def _calc_whale_score(top10: float | None) -> int:
    """Whale risk score (8% weight)."""
    if top10 is None:
        return 50
    if top10 <= 0:
        return 50
    if top10 < 20: return 100
    if top10 <= 30: return 85
    if top10 <= 40: return 70
    if top10 <= 50: return 55
    if top10 <= 60: return 40
    if top10 <= 70: return 25
    if top10 <= 80: return 15
    return 5


def _calc_overall_v31_legacy(
    momentum: int, volume: int, trade: int, liquidity: int,
    holder: int, security: int, whale: int,
    market_cap: float,
    token_info: dict, holder_data: dict, liq_usd: float, age_hours: float | None, holder_count: int
) -> dict:
    """
    [LEGACY — kept for rollback / reference]
    Overall score v3.1: Technical 65% / On-Chain 35% / Social 0%.

    Superseded by _calc_overall_v40 (Security 40% / Market 35% / Momentum 25%),
    which is RugCheck-primary and YAML-driven. This function is no longer
    called from the analyze pipeline but is retained so historical behavior
    can be reproduced if needed.
    """
    # Technical (65%)
    tech = momentum * 0.20 + volume * 0.15 + trade * 0.10 + liquidity * 0.20
    
    # On-Chain (35%)
    onchain = holder * 0.12 + security * 0.13 + whale * 0.10
    
    raw = tech + onchain
    
    # Override rules
    cap = 100
    overrides = []
    
    is_established = holder_count > 10000 or (age_hours is not None and age_hours > 720)
    is_large = liq_usd > 1000000 or holder_count > 50000
    
    # Mint authority
    if token_info.get("mint_authority") and not token_info.get("is_mint_renounced"):
        if is_large:
            cap = min(cap, 70)
            overrides.append(
                "Mint authority is active (large-cap) — supply can be increased; max score 70/100"
            )
        elif is_established:
            cap = min(cap, 55)
            overrides.append(
                "Mint authority is active (established) — supply can be increased; max score 55/100"
            )
        else:
            cap = min(cap, 35)
            overrides.append(
                "Mint authority is active — the team can print more tokens; max score 35/100"
            )
    
    # Low liquidity (stronger caps)
    if liq_usd > 0 and liq_usd < 10_000:
        if is_established:
            cap = min(cap, 50)
            overrides.append(
                f"Very low liquidity (${liq_usd:,.0f}) — small trades can move price a lot; max score 50/100"
            )
        else:
            cap = min(cap, 30)
            overrides.append(
                f"Very low liquidity (${liq_usd:,.0f}) — easy to manipulate and hard to exit; max score 30/100"
            )
    elif liq_usd > 0 and liq_usd < 50_000:
        cap = min(cap, 45 if is_established else 35)
        overrides.append(
            f"Low liquidity (${liq_usd:,.0f}) — higher slippage and exit risk; max score {cap}/100"
        )
    elif liq_usd > 0 and liq_usd < 100_000 and not is_established:
        cap = min(cap, 55)
        overrides.append(
            f"Moderate-low liquidity (${liq_usd:,.0f}) — exit risk is still meaningful; max score 55/100"
        )

    # Low market cap (prevents tiny tokens from scoring too high)
    mc = float(market_cap) if isinstance(market_cap, (int, float)) else 0.0
    if mc and mc > 0:
        if mc < 100_000:
            cap_before = cap
            cap = min(cap, 45 if is_established else 35)
            if cap < cap_before:
                overrides.append(
                    f"Very low market cap (${mc:,.0f}) — early-stage token, easier to manipulate; max score {cap}/100"
                )
        elif mc < 300_000 and not is_established:
            cap_before = cap
            cap = min(cap, 55)
            if cap < cap_before:
                overrides.append(
                    f"Low market cap (${mc:,.0f}) — higher volatility and liquidity risk; max score {cap}/100"
                )
        elif mc < 1_000_000 and not is_established:
            cap_before = cap
            cap = min(cap, 70)
            if cap < cap_before:
                overrides.append(
                    f"Small market cap (${mc:,.0f}) — higher risk than established tokens; max score {cap}/100"
                )
    
    # Whale concentration (stronger caps)
    raw_top10 = holder_data.get("top10_pct") if isinstance(holder_data, dict) else None
    top10 = float(raw_top10) if isinstance(raw_top10, (int, float)) else None
    raw_largest = holder_data.get("largest_holder_pct") if isinstance(holder_data, dict) else None
    largest = float(raw_largest) if isinstance(raw_largest, (int, float)) else None

    if top10 is not None and top10 > 80:
        if is_large:
            cap = min(cap, 65)
            overrides.append(
                f"Top 10 wallets hold {top10:.1f}% of supply (large-cap) — a few wallets can crash the price; max score 65/100"
            )
        elif is_established:
            cap = min(cap, 50)
            overrides.append(
                f"Top 10 wallets hold {top10:.1f}% of supply (established) — high dump risk; max score 50/100"
            )
        else:
            cap = min(cap, 30)
            overrides.append(
                f"Top 10 wallets hold {top10:.1f}% of supply — a few wallets control the price; max score 30/100"
            )
    elif top10 is not None and top10 > 70:
        cap = min(cap, 55 if is_established else 40)
        overrides.append(
            f"Top 10 wallets hold {top10:.1f}% of supply — high concentration; max score {cap}/100"
        )
    elif top10 is not None and top10 > 60 and not is_large:
        cap = min(cap, 65 if is_established else 50)
        overrides.append(
            f"Top 10 wallets hold {top10:.1f}% of supply — concentration risk; max score {cap}/100"
        )

    if largest is not None and largest > 25:
        cap = min(cap, 40 if is_established else 30)
        overrides.append(
            f"Largest holder owns {largest:.1f}% — single-wallet dump risk; max score {cap}/100"
        )
    
    # Low holders
    if holder_count and holder_count < 100:
        cap = min(cap, 35)
        overrides.append(
            f"Very few holders ({holder_count}) — easy for insiders to control price; max score 35/100"
        )
    elif holder_count and holder_count < 500 and not is_established:
        cap = min(cap, 45)
        overrides.append(
            f"Low holder count ({holder_count}) — price can be unstable; max score 45/100"
        )
    
    # New token (only if age is known)
    if age_hours is not None and age_hours < 24:
        if holder_count > 1000:
            cap = min(cap, 55)
            overrides.append(
                f"Token is very new ({age_hours:.1f}h old) — early hype can reverse fast; max score 55/100"
            )
        else:
            cap = min(cap, 40)
            overrides.append(
                f"Token is very new ({age_hours:.1f}h old) — highest rug/volatility window; max score 40/100"
            )
    
    # Freeze authority
    if token_info.get("freeze_authority"):
        if is_large:
            cap = min(cap, 75)
            overrides.append(
                "Freeze authority is active (large-cap) — token accounts can be frozen; max score 75/100"
            )
        else:
            cap = min(cap, 50)
            overrides.append(
                "Freeze authority is active — token accounts can be frozen; max score 50/100"
            )
    
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
# v4.0 SCORING — RugCheck-primary, YAML-driven
# Weights: Security(40%) + Market(35%) + Momentum(25%)
# ══════════════════════════════════════════════════════════════════════════════

def _v40_security_subscores(rugcheck: dict | None, token_info: dict, age_hours: float | None) -> dict:
    """
    Security bucket sub-scores (0-100 each).
    RugCheck is primary source. When unavailable, falls back to on-chain RPC.
    """
    sec_cfg = scoring_loader.get_section("security")
    lp_cfg = sec_cfg.get("lp_lock", {})

    # Mint / freeze authority
    if rugcheck is not None:
        mint_active = bool(rugcheck.get("mint_authority_active"))
        freeze_active = bool(rugcheck.get("freeze_authority_active"))
    else:
        mint_active = bool(token_info.get("mint_authority")) and not token_info.get("is_mint_renounced")
        freeze_active = bool(token_info.get("freeze_authority"))

    mint_score = sec_cfg.get("mint", {}).get("disabled_score", 100) if not mint_active \
        else sec_cfg.get("mint", {}).get("active_score", 0)
    freeze_score = sec_cfg.get("freeze", {}).get("disabled_score", 100) if not freeze_active \
        else sec_cfg.get("freeze", {}).get("active_score", 20)

    # LP lock/burn (FIX for long-standing TODO in solana_scraper.py:362)
    lp_locked_pct = 0.0
    if rugcheck is not None:
        lp_locked_pct = float(rugcheck.get("lp_locked_pct") or 0.0)
    full_burn = lp_cfg.get("full_burn_pct", 80)
    if lp_locked_pct >= full_burn:
        lp_score = lp_cfg.get("burn_score", 100)
    elif lp_locked_pct >= 20:
        lp_score = lp_cfg.get("partial_score", 60)
    else:
        # Unknown ≠ unlocked. Without RugCheck data we can't penalise — use partial.
        lp_score = lp_cfg.get("unlocked_score", 10) if rugcheck is not None else lp_cfg.get("partial_score", 60)

    # Insider clusters (via RugCheck bundler + insider %)
    insider_pct = float(rugcheck.get("insider_pct") or 0.0) if rugcheck else 0.0
    bundler_pct = float(rugcheck.get("bundler_pct") or 0.0) if rugcheck else 0.0
    combined = insider_pct + bundler_pct * 0.5  # bundlers less severe than persistent insiders
    if rugcheck is None:
        insider_score = 60          # no data, neutral-positive
    elif combined < 5:
        insider_score = 100
    elif combined < 10:
        insider_score = 75
    elif combined < 20:
        insider_score = 45
    elif combined < 30:
        insider_score = 25
    else:
        insider_score = 5

    return {
        "mint": int(mint_score),
        "freeze": int(freeze_score),
        "lp_locked": int(lp_score),
        "insider": int(insider_score),
        "lp_locked_pct": lp_locked_pct,
        "insider_pct": insider_pct,
        "bundler_pct": bundler_pct,
        "mint_active": mint_active,
        "freeze_active": freeze_active,
    }


def _v40_momentum_subscores(
    momentum_score: int,
    realized_vol: float | None,
    volume_trend_delta: float | None,
) -> dict:
    vol_cfg = scoring_loader.get_section("volatility")
    vt_cfg = scoring_loader.get_section("volume_trend")
    vol_score = scoring_loader.tier_score(
        realized_vol,
        vol_cfg.get("tiers", []),
        vol_cfg.get("scores", []),
        below_min=vol_cfg.get("above_max_score", 5),
        unknown=vol_cfg.get("insufficient_data_score", 50),
        descending=False,
    )
    vt_score = scoring_loader.tier_score(
        volume_trend_delta,
        vt_cfg.get("tiers", []),
        vt_cfg.get("scores", []),
        below_min=vt_cfg.get("below_min_score", 15),
        unknown=50,
    )
    return {
        "price_action": int(momentum_score),
        "volatility": int(vol_score),
        "volume_trend": int(vt_score),
    }


def _v40_market_subscores(
    liquidity_score: int,
    volume_score: int,
    trade_pressure_score: int,
    whale_score: int,
    wash_trade_score: float | None,
    vol_mcap_ratio: float | None = None,
) -> dict:
    """
    Market sub-scores. `vol_mcap_ratio` is the raw volume_24h / market_cap
    number — when supplied, we tier it against the YAML `volume_mcap` config
    directly (which has the wash-trade ceiling and manipulation tiers).
    Falls back to the legacy composite `volume_score` when the ratio is not
    provided (e.g. market_cap unknown).
    """
    # wash_trade_score is 0-1 where 1 = full wash. Invert to 0-100 where 100 = healthy.
    wash_0_100 = 60 if wash_trade_score is None else int(round((1.0 - max(0.0, min(1.0, wash_trade_score))) * 100))

    if vol_mcap_ratio is not None:
        vm_cfg = scoring_loader.get_section("volume_mcap") or {}
        ceiling = vm_cfg.get("wash_trade_ceiling", 5.0)
        manip = vm_cfg.get("manipulation_tier", 2.0)
        hot = vm_cfg.get("hot_trade_tier", 1.0)
        if vol_mcap_ratio >= ceiling:
            vm_score = 15          # statistically impossible = wash
        elif vol_mcap_ratio >= manip:
            vm_score = 30
        elif vol_mcap_ratio > hot:
            vm_score = 45
        else:
            vm_score = scoring_loader.tier_score(
                vol_mcap_ratio,
                vm_cfg.get("tiers", []),
                vm_cfg.get("scores", []),
                below_min=vm_cfg.get("below_min_score", 15),
                unknown=50,
            )
    else:
        vm_score = int(volume_score)

    return {
        "liquidity": int(liquidity_score),
        "volume_mcap": int(vm_score),
        "buy_sell": int(trade_pressure_score),
        "top10": int(whale_score),
        "wash": wash_0_100,
    }


def _v40_apply_override_caps(
    cap: int,
    overrides: list,
    holder_count: int,
    age_hours: float | None,
    liq_usd: float,
    market_cap: float,
    token_info: dict,
    holder_data: dict,
    rugcheck: dict | None,
    wash_trade_score: float | None,
) -> tuple[int, list]:
    """
    Apply v4.0 YAML-driven override caps.

    Pattern: each rule is a {new, established, large} triple; the rule picks
    the cap that matches the token's maturity class, else skips (null).
    """
    caps_cfg = scoring_loader.get_section("override_caps") or {}
    est_cfg = caps_cfg.get("established", {}) or {}
    lg_cfg = caps_cfg.get("large", {}) or {}

    is_established = (holder_count > (est_cfg.get("holders_min", 10000))) or \
                     (age_hours is not None and age_hours > est_cfg.get("age_hours_min", 720))
    is_large = (liq_usd > (lg_cfg.get("liquidity_usd_min", 1_000_000))) or \
               (holder_count > lg_cfg.get("holders_min", 50000))

    def apply(rule_key: str, condition: bool, msg: str):
        nonlocal cap
        if not condition:
            return
        rule = caps_cfg.get(rule_key) or {}
        klass = "large" if is_large else ("established" if is_established else "new")
        ceiling = rule.get(klass)
        if ceiling is None:
            return
        # Always record the risk factor for LLM/user visibility; cap
        # only tightens to the lowest ceiling across rules.
        cap = min(cap, ceiling)
        overrides.append(f"{msg} (max {ceiling}/100)")

    # Mint / Freeze
    if rugcheck is not None:
        mint_active = bool(rugcheck.get("mint_authority_active"))
        freeze_active = bool(rugcheck.get("freeze_authority_active"))
    else:
        mint_active = bool(token_info.get("mint_authority")) and not token_info.get("is_mint_renounced")
        freeze_active = bool(token_info.get("freeze_authority"))
    apply("mint_active", mint_active, "Mint authority active — supply can be inflated")
    apply("freeze_active", freeze_active, "Freeze authority active — wallets can be frozen")

    # Liquidity
    apply("liquidity_under_10k", 0 < liq_usd < 10_000, f"Very low liquidity (${liq_usd:,.0f})")
    apply("liquidity_10k_50k", 10_000 <= liq_usd < 50_000, f"Low liquidity (${liq_usd:,.0f})")
    apply("liquidity_50k_100k", 50_000 <= liq_usd < 100_000, f"Moderate-low liquidity (${liq_usd:,.0f})")

    # Market cap
    mc = float(market_cap) if isinstance(market_cap, (int, float)) else 0.0
    if mc > 0:
        apply("mcap_under_100k", mc < 100_000, f"Very low market cap (${mc:,.0f})")
        apply("mcap_100k_300k", 100_000 <= mc < 300_000, f"Low market cap (${mc:,.0f})")
        apply("mcap_300k_1m", 300_000 <= mc < 1_000_000, f"Small market cap (${mc:,.0f})")

    # Concentration
    top10 = holder_data.get("top10_pct") if isinstance(holder_data, dict) else None
    largest = holder_data.get("largest_holder_pct") if isinstance(holder_data, dict) else None
    top10 = float(top10) if isinstance(top10, (int, float)) else None
    largest = float(largest) if isinstance(largest, (int, float)) else None
    if top10 is not None:
        apply("top10_over_80", top10 > 80, f"Top 10 hold {top10:.1f}%")
        apply("top10_over_70", 70 < top10 <= 80, f"Top 10 hold {top10:.1f}%")
        apply("top10_over_60", 60 < top10 <= 70, f"Top 10 hold {top10:.1f}%")
    if largest is not None:
        apply("largest_over_25", largest > 25, f"Largest holder owns {largest:.1f}%")

    # Holders
    apply("holders_under_100", bool(holder_count) and holder_count < 100,
          f"Very few holders ({holder_count})")
    apply("holders_under_500_new", bool(holder_count) and 100 <= holder_count < 500,
          f"Low holder count ({holder_count})")

    # Age
    if age_hours is not None and age_hours < 24:
        apply("age_under_24h_growing", holder_count > 1000, f"Very new token ({age_hours:.1f}h)")
        apply("age_under_24h_small", holder_count <= 1000, f"Very new token ({age_hours:.1f}h)")

    # v4.0-new: RugCheck-driven caps
    if rugcheck is not None:
        ins = float(rugcheck.get("insider_pct") or 0.0)
        apply("insider_pct_over_30", ins > 30, f"Insider wallets hold {ins:.1f}% (RugCheck)")
        lp = float(rugcheck.get("lp_locked_pct") or 0.0)
        apply("lp_unlocked", lp < 20, f"LP not locked/burned ({lp:.0f}% locked)")
    if wash_trade_score is not None:
        apply("wash_trade_score_high", wash_trade_score > 0.7,
              f"Wash-trade signal high ({wash_trade_score:.2f})")

    return cap, overrides


def _calc_rug_score_composite(
    rugcheck: dict | None,
    holder_data: dict,
    token_info: dict,
) -> dict:
    """
    Composite rug score 0-100 (HIGHER = riskier). Independent of the trust score.
    Uses RugCheck where available; falls back to holder_data / token_info.
    """
    cfg = scoring_loader.get_section("rug_score") or {}
    w = cfg.get("weights", {}) or {}

    def _f(v, default=0.0) -> float:
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    insider_pct = _f(rugcheck.get("insider_pct") if rugcheck else 0.0)
    lp_locked_pct = _f(rugcheck.get("lp_locked_pct") if rugcheck else 0.0)
    lp_unlocked_pct = max(0.0, 100.0 - lp_locked_pct)
    top10_pct = _f(rugcheck.get("top_holders_pct") if rugcheck else None) or _f(holder_data.get("top10_pct") if isinstance(holder_data, dict) else None)
    sniper_pct = _f(rugcheck.get("sniper_pct") if rugcheck else 0.0)
    bundler_pct = _f(rugcheck.get("bundler_pct") if rugcheck else 0.0)

    if rugcheck is not None:
        mint_active = bool(rugcheck.get("mint_authority_active"))
    else:
        mint_active = bool(token_info.get("mint_authority")) and not token_info.get("is_mint_renounced")
    mint_penalty = cfg.get("mint_active_value", 50) if mint_active else 0

    rug = (
        insider_pct * w.get("insider_pct", 0.25) +
        lp_unlocked_pct * w.get("lp_unlocked_pct", 0.25) +
        top10_pct * w.get("top10_pct", 0.20) +
        sniper_pct * w.get("sniper_pct", 0.15) +
        bundler_pct * w.get("bundler_pct", 0.10) +
        mint_penalty * w.get("mint_active_penalty", 0.05)
    )
    rug = max(0.0, min(100.0, rug))

    return {
        "rug_score": round(rug, 1),
        "breakdown": {
            "insider_pct": round(insider_pct, 2),
            "lp_unlocked_pct": round(lp_unlocked_pct, 2),
            "sniper_pct": round(sniper_pct, 2),
            "bundler_pct": round(bundler_pct, 2),
            "top10_pct": round(top10_pct, 2),
        },
    }


def _calc_overall_v40(
    momentum: int,
    volume: int,
    trade: int,
    liquidity: int,
    holder: int,
    security_legacy: int,
    whale: int,
    market_cap: float,
    token_info: dict,
    holder_data: dict,
    liq_usd: float,
    age_hours: float | None,
    holder_count: int,
    rugcheck: dict | None = None,
    wash_trade_score: float | None = None,
    realized_vol: float | None = None,
    volume_trend_delta: float | None = None,
    vol_mcap_ratio: float | None = None,
) -> dict:
    """
    Overall score v4.0.

    Blend: Security(40%) + Market(35%) + Momentum(25%).
    RugCheck drives security when available; YAML drives all thresholds and
    override caps.

    Returns a dict with the same keys as legacy (score/risk/raw/cap/overrides/
    tech_total/onchain_total) PLUS new v4.0 fields so downstream code keeps
    working unchanged.
    """
    weights = scoring_loader.get_weights() or {}
    w_sec = float(weights.get("security", 0.40))
    w_mkt = float(weights.get("market", 0.35))
    w_mom = float(weights.get("momentum", 0.25))

    sec_sub = _v40_security_subscores(rugcheck, token_info, age_hours)
    mkt_sub = _v40_market_subscores(liquidity, volume, trade, whale, wash_trade_score, vol_mcap_ratio=vol_mcap_ratio)
    mom_sub = _v40_momentum_subscores(momentum, realized_vol, volume_trend_delta)

    sc = weights.get("security_components", {}) or {}
    mc = weights.get("market_components", {}) or {}
    mmc = weights.get("momentum_components", {}) or {}

    security_total = (
        sec_sub["mint"] * float(sc.get("mint_authority_disabled", 0.25)) +
        sec_sub["freeze"] * float(sc.get("freeze_authority_disabled", 0.25)) +
        sec_sub["lp_locked"] * float(sc.get("lp_locked_or_burned", 0.25)) +
        sec_sub["insider"] * float(sc.get("no_insider_clusters", 0.25))
    )
    market_total = (
        mkt_sub["liquidity"] * float(mc.get("liquidity_tier", 0.286)) +
        mkt_sub["volume_mcap"] * float(mc.get("volume_mcap_ratio", 0.229)) +
        mkt_sub["buy_sell"] * float(mc.get("buy_sell_pressure", 0.200)) +
        mkt_sub["top10"] * float(mc.get("top10_holder_pct", 0.143)) +
        mkt_sub["wash"] * float(mc.get("wash_trade_score", 0.143))
    )
    momentum_total = (
        mom_sub["price_action"] * float(mmc.get("price_action_multi_tf", 0.40)) +
        mom_sub["volatility"] * float(mmc.get("realized_volatility", 0.32)) +
        mom_sub["volume_trend"] * float(mmc.get("volume_trend_delta", 0.28))
    )

    raw = security_total * w_sec + market_total * w_mkt + momentum_total * w_mom

    cap, overrides = _v40_apply_override_caps(
        cap=100, overrides=[],
        holder_count=holder_count, age_hours=age_hours, liq_usd=liq_usd,
        market_cap=market_cap, token_info=token_info, holder_data=holder_data,
        rugcheck=rugcheck, wash_trade_score=wash_trade_score,
    )

    final = max(0, min(100, min(int(raw), cap)))
    risk = scoring_loader.get_risk_level(final)

    # Backward-compat synthesized fields for downstream display code
    tech_total_legacy = market_total * w_mkt + momentum_total * w_mom
    onchain_total_legacy = security_total * w_sec

    return {
        "score": final,
        "risk": risk,
        "raw": int(raw),
        "cap": cap if cap < 100 else None,
        "overrides": overrides,
        "tech_total": tech_total_legacy,
        "onchain_total": onchain_total_legacy,
        # v4.0 native
        "version": "4.0",
        "security_total": round(security_total, 1),
        "market_total": round(market_total, 1),
        "momentum_total": round(momentum_total, 1),
        "components": {
            "security": sec_sub,
            "market": mkt_sub,
            "momentum": mom_sub,
        },
        "weights": {"security": w_sec, "market": w_mkt, "momentum": w_mom},
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


# ══════════════════════════════════════════════════════════════════════════════
# PUMPSCAN MONITOR ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/watching")
def get_watching(db: Session = Depends(get_db)):
    """Return all watched coins with events for the PumpScan dashboard."""
    return crud.get_all_watched(db)


@router.get("/api/events")
def get_events(limit: int = 500, db: Session = Depends(get_db)):
    """Return recent events across all watched coins."""
    return crud.get_events(db, limit=limit)


@router.get("/api/dashboard-stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """Return aggregate stats for the PumpScan dashboard."""
    return crud.get_dashboard_stats(db)


class WatchRequest(BaseModel):
    mint: str

@router.post("/api/watch")
async def watch_coin(req: WatchRequest):
    """User adds a coin to watch list. Runs T0-T4 pipeline."""
    from monitor.coin_adder import add_coin
    return await add_coin(req.mint)


@router.post("/api/unwatch")
async def unwatch_coin(req: WatchRequest):
    """User removes a coin from watch list."""
    from monitor.coin_adder import remove_coin
    return await remove_coin(req.mint)


@router.post("/api/monitor/deep-scan")
async def deep_scan_coin(req: WatchRequest):
    """Trigger Tier 2 deep scan (T1-T4) for a pending/degraded coin."""
    from monitor.lp_detector import run_deep_scan
    return await run_deep_scan(req.mint)


@router.post("/api/monitor/ai-check")
async def monitor_ai_check(req: WatchRequest, db: Session = Depends(get_db)):
    """On-demand AI analysis using OpenGradient Claude Sonnet 4.6."""
    from database.models import WatchedCoin
    import json, time as _time

    coin = db.query(WatchedCoin).filter(WatchedCoin.mint == req.mint).first()
    if not coin:
        return {"ok": False, "error": "Coin not found"}

    sm = json.loads(coin.smart_money) if coin.smart_money else []
    context = {
        "symbol": coin.symbol,
        "mint": coin.mint,
        "dex": coin.dex,
        "mcap": coin.mcap or 0,
        "liq": coin.liq or 0,
        "holders": coin.holders or 0,
        "score": coin.score,
        "status": coin.status,
        "smart_money_count": len(sm),
        "pass_count": sum([coin.filter_t0, coin.filter_t1, coin.filter_t2, coin.filter_t3, coin.filter_t4]),
        "filter": {"t0": coin.filter_t0, "t1": coin.filter_t1, "t2": coin.filter_t2, "t3": coin.filter_t3, "t4": coin.filter_t4},
        "pump_created_at": coin.pump_created_at,
        "lp_added_at": coin.lp_added_at,
    }

    # Build analysis input
    on_chain_data = {
        "market_cap": context["mcap"],
        "liquidity": context["liq"],
        "holders": context["holders"],
        "smart_money_wallets": len(sm),
        "dex": context["dex"],
        "filter_pipeline": f"{context['pass_count']}/5 pass",
    }
    scoring_result = {
        "overall_score": coin.score,
        "filter_t0": coin.filter_t0, "filter_t1": coin.filter_t1,
        "filter_t2": coin.filter_t2, "filter_t3": coin.filter_t3, "filter_t4": coin.filter_t4,
    }

    try:
        from analysis.opengradient import OpenGradientAnalyzer
        analyzer = OpenGradientAnalyzer()
        result = await analyzer.analyze_coin(
            ticker=coin.symbol,
            tweets_summary=[],
            on_chain_data=on_chain_data,
            scoring_result=scoring_result,
            llm_model="anthropic/claude-sonnet-4-6",
        )

        # Store in DB
        ai_data = result.get("ai_result", {})
        coin.ai_analysis = json.dumps(ai_data)
        coin.ai_scanned_at = int(_time.time())
        db.commit()

        return {"ok": True, "analysis": ai_data, "model": "claude-sonnet-4-6"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/monitor/purge-old")
async def purge_old_coins(db: Session = Depends(get_db)):
    """Remove coins older than 24h (pending) or 7d (removed) to keep dashboard clean."""
    from database.models import WatchedCoin, EventLog
    import time as _time
    now = int(_time.time())
    removed = 0

    # Remove pending coins older than 6h
    old_pending = db.query(WatchedCoin).filter(
        WatchedCoin.status == "pending",
        WatchedCoin.lp_added_at < now - 6 * 3600,
    ).all()
    for c in old_pending:
        db.query(EventLog).filter(EventLog.mint == c.mint).delete()
        db.delete(c)
        removed += 1

    # Remove "removed" coins older than 7d
    old_removed = db.query(WatchedCoin).filter(
        WatchedCoin.status == "removed",
        WatchedCoin.last_checked < now - 7 * 86400,
    ).all()
    for c in old_removed:
        db.query(EventLog).filter(EventLog.mint == c.mint).delete()
        db.delete(c)
        removed += 1

    db.commit()
    return {"ok": True, "removed": removed}


@router.post("/api/monitor/seed")
async def seed_from_ws():
    """Collect pump.fun migrations via WebSocket for 120s to seed the DB."""
    from monitor.pumpfun_ws import ws_seed_collect
    count = await ws_seed_collect(duration_sec=120)
    return {"ok": True, "count": count, "message": f"Collected {count} migrations in 120s"}


@router.get("/api/monitor/ws-status")
async def ws_status():
    """Return WebSocket connection stats."""
    from monitor.pumpfun_ws import get_ws_stats
    from database.engine import SessionLocal
    from database.models import WatchedCoin
    db = SessionLocal()
    try:
        total = db.query(WatchedCoin).count()
        pending = db.query(WatchedCoin).filter(WatchedCoin.status == "pending").count()
        active = db.query(WatchedCoin).filter(WatchedCoin.status == "active").count()
    finally:
        db.close()
    stats = get_ws_stats()
    stats["db_total"] = total
    stats["db_pending"] = pending
    stats["db_active"] = active
    return stats


# ── Quick Token Info (Helius-only, 0 Birdeye) ──────────────────────
_quick_info_cache: dict[str, tuple[float, dict]] = {}  # mint -> (timestamp, data)
QUICK_INFO_TTL = 300  # 5 min cache


@router.get("/api/token/quick-info")
async def get_token_quick_info(mint: str):
    """Lightweight on-chain token info via Helius RPC. FREE — zero Birdeye calls."""
    import time as _time

    # Cache check
    if mint in _quick_info_cache:
        ts, data = _quick_info_cache[mint]
        if _time.time() - ts < QUICK_INFO_TTL:
            return data

    from scrapers.solana_scraper import SolanaScraper
    scraper = SolanaScraper()

    token_info = await scraper.get_token_info(mint)
    if not token_info:
        raise HTTPException(status_code=404, detail="Token not found on-chain")

    holders_data = await scraper.get_top_holders(mint, top_n=20, prefetched_token_info=token_info)

    result = {
        "mint": token_info.get("mint", mint),
        "symbol": token_info.get("symbol"),
        "supply": token_info.get("supply"),
        "decimals": token_info.get("decimals"),
        "mint_authority": token_info.get("mint_authority"),
        "freeze_authority": token_info.get("freeze_authority"),
        "is_mint_renounced": token_info.get("is_mint_renounced", False),
        "top10_pct": holders_data.get("top10_pct") if holders_data else None,
        "top20_pct": holders_data.get("top20_pct") if holders_data else None,
        "largest_holder_pct": holders_data.get("largest_holder_pct") if holders_data else None,
        "top_holders": (holders_data.get("holders") or [])[:10] if holders_data else [],
    }

    _quick_info_cache[mint] = (_time.time(), result)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SUI/USDT Spot Forecasting (OpenGradient ML Models)
# ═══════════════════════════════════════════════════════════════════════════════

from analysis.forecast import (
    fetch_current_price,
    fetch_price_history,
    fetch_ohlc,
    run_inference,
    record_prediction,
    get_prediction_history,
    MODELS,
)


@router.get("/forecast/price")
async def forecast_price(interval: str = "15m", limit: int = 200):
    """Current SUI/USDT price + chart candle data."""
    try:
        ticker, history = await asyncio.gather(
            fetch_current_price(),
            fetch_price_history(interval=interval, limit=limit),
        )
        return {"ticker": ticker, "candles": history, "interval": interval}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Binance API error: {e}")


@router.get("/forecast/models")
async def forecast_models():
    """List available forecast models and their specs."""
    return {
        key: {
            "name": m["name"],
            "horizon": m["horizon"],
            "candles": m["candles"],
            "interval": m["interval"],
            "accuracy": m["accuracy"],
            "mse": m["mse"],
            "correlation": m["correlation"],
            "has_cid": bool(m["cid"]),
        }
        for key, m in MODELS.items()
    }


@router.post("/forecast/predict/{model_key}")
async def forecast_predict(model_key: str):
    """Run spot forecast inference for SUI/USDT."""
    if model_key not in MODELS:
        raise HTTPException(status_code=400, detail=f"Model must be one of: {list(MODELS.keys())}")

    # Fix: Binance doesn't support 3h interval, force 4h
    if MODELS[model_key].get("binance_interval") == "3h":
        MODELS[model_key]["binance_interval"] = "4h"

    try:
        result = await run_inference(model_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])

    record_prediction(result)
    return result


@router.get("/forecast/history")
async def forecast_history():
    """Get recent prediction history."""
    return {"predictions": get_prediction_history()}


@router.post("/forecast/interpret")
async def forecast_interpret(request: dict):
    """Stream AI interpretation of dual SUI/USDT predictions."""
    body = request or {}
    pred30 = body.get("pred30")
    pred6h = body.get("pred6h")

    if not pred30 and not pred6h:
        raise HTTPException(status_code=400, detail="No prediction data provided")

    # Build prompt
    lines = ["Briefly interpret these SUI/USDT ML forecast results for a crypto trader (2-3 sentences, Vietnamese if possible):"]
    if pred30:
        lines.append(f"- 30min model: {pred30.get('direction')} {pred30.get('predicted_return', 0):+.3f}% → ${pred30.get('predicted_price', 0):.4f} (accuracy {pred30.get('confidence', 53)}%)")
    if pred6h:
        lines.append(f"- 6h model: {pred6h.get('direction')} {pred6h.get('predicted_return', 0):+.3f}% → ${pred6h.get('predicted_price', 0):.4f} (accuracy {pred6h.get('confidence', 53)}%)")

    current = (pred30 or pred6h).get("current_price", 0)
    lines.append(f"- Current price: ${current:.4f}")
    lines.append("Focus on: model agreement, signal strength, risk, and whether this is actionable. Be concise and direct.")

    prompt = "\n".join(lines)

    async def event_gen():
        try:
            async for chunk in opengradient_analyzer.chat_completion_stream(
                messages=[{"role": "user", "content": prompt}],
                llm_model="anthropic/claude-haiku-4-5",
                max_tokens=300,
            ):
                yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


