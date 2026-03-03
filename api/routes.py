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

_ANALYZE_STREAM_META_CB: ContextVar[Optional[Callable[[dict], Awaitable[None]]]] = ContextVar(
    "_ANALYZE_STREAM_META_CB", default=None
)

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
        model = str(value).strip().upper()
        return model or None

    allowed_llm_models = {
        "GPT_4_1_2025_04_14",
        "O4_MINI",
        "GPT_5",
        "GPT_5_MINI",
        "GPT_5_2",
        "CLAUDE_SONNET_4_5",
        "CLAUDE_SONNET_4_6",
        "CLAUDE_HAIKU_4_5",
        "CLAUDE_OPUS_4_5",
        "CLAUDE_OPUS_4_6",
        "GEMINI_2_5_FLASH",
        "GEMINI_2_5_PRO",
        "GEMINI_2_5_FLASH_LITE",
        "GEMINI_3_PRO",
        "GEMINI_3_FLASH",
        "GROK_4",
        "GROK_4_FAST",
        "GROK_4_1_FAST",
        "GROK_4_1_FAST_NON_REASONING",
    }

    normalized_model = _normalize_llm_model(llm_model)
    if normalized_model and normalized_model not in allowed_llm_models:
        logger.warning(f"Invalid llm_model requested: {normalized_model}. Falling back to default.")
        normalized_model = None
    effective_model = normalized_model or "CLAUDE_SONNET_4_5"
    
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
                                    chain_type = "solana" if target_chain == "solana" else "evm"
                                    logger.info(f"✅ Resolved {ticker} → {contract_address[:8]}... ({target_chain})")
                                    break
            except Exception as e:
                logger.error(f"❌ DexScreener search failed: {e}")

    if not contract_address:
        raise HTTPException(status_code=400, detail="Unable to resolve contract address")

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
            logger.info(f"� Fetching DexScreener...")
            try:
                async with httpx.AsyncClient() as client:
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
            except Exception as e:
                logger.warning(f"⚠️ DexScreener failed: {e}")
            
            # 2. Chain-specific data fetching
            if chain_type == "solana":
                # Solana RPC FIRST - to resolve the mint address (Birdeye needs Base58)
                logger.info(f"� Fetching Solana RPC for: {contract_address}")
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
                    holder_data = await solana_scraper.get_top_holders(contract_address) or {}
                    logger.info(f"✅ Solana RPC: mint_renounced={token_info.get('is_mint_renounced')}")
                except HTTPException:
                    raise
                except Exception as e:
                    logger.warning(f"⚠️ Solana RPC failed: {e}")
                    raise HTTPException(
                        status_code=502,
                        detail="Solana RPC validation failed. Unable to confirm this is a token mint address.",
                    )
                
                # Birdeye (Solana only) - AFTER address is resolved
                logger.info(f"� Fetching Birdeye for address: {contract_address}")
                try:
                    cache_key = CacheKeys.birdeye_overview(contract_address)
                    raw = None
                    if not force_refresh:
                        raw = await cache.get(cache_key)
                    if not raw:
                        raw = await birdeye_client.get_token_overview(contract_address)
                        if raw:
                            await cache.set(cache_key, raw, CacheTTL.BIRDEYE_OVERVIEW)
                    if raw:
                        birdeye_data = birdeye_client.extract_scores_from_overview(raw)
                        birdeye_data["raw"] = raw
                        birdeye_used = True
                        if isinstance(holder_data, dict) and birdeye_data.get("holder_count") and not holder_data.get("total_holders"):
                            holder_data["total_holders"] = birdeye_data.get("holder_count")
                        mc = birdeye_data.get('market_cap') or 0
                        holders = birdeye_data.get('holder_count') or 0
                        vol = birdeye_data.get('volume_24h') or 0
                        logger.info(f"✅ Birdeye: MC=${mc:,.0f}, Holders={holders:,}, Vol=${vol:,.0f}")
                except Exception as e:
                    logger.warning(f"⚠️ Birdeye failed: {e}")

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
        
        overall = _calc_overall_v31(
            momentum=momentum_result.get("momentum_score", 50),
            volume=volume_result.get("volume_score", 50),
            trade=trade_result.get("trade_pressure_score", 50),
            liquidity=liquidity_result.get("liquidity_score", 50),
            holder=holder_score,
            security=security_result.get("security_score", 50),
            whale=whale_score,
            market_cap=market_cap,
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

        on_chain_context = {
            "holders": holder_data,
            "token_info": token_info,
            "liquidity_usd": liq_usd,
            "market_data": market_data,
            "smart_money": {} # Disable for now to save tokens
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
        
        logger.info(f"🧠 Running OpenGradient AI Inference...")
        stream_meta_cb = _ANALYZE_STREAM_META_CB.get()
        if stream_meta_cb:
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
        ai_error = ai_result.get("error") if isinstance(ai_result, dict) else None
        if ai_error:
            logger.error(f"❌ OpenGradient error: {ai_error}")
            raise HTTPException(status_code=502, detail=f"OpenGradient AI processing failed: {ai_error}")
        ai_trust_score = None
        ai_inference_success = False
        tx_hash = None
        payment_hash = None
        settlement_mode = None
        verification = None
        verify_url = None
        verdict = ""
        
        if ai_result.get("ai_result") and ai_result.get("used_opengradient"):
            ai_data = ai_result["ai_result"]
            
            # Chỉ dùng AI score nếu THẬT SỰ thành công (không phải default)
            if not ai_data.get("verdict", "").startswith("Phân tích AI thất bại"):
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
                logger.info(f"✅ AI inference SUCCESS: trust={ai_trust_score}, tx={tx_hash}")
                
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
        
        # Generate verdict từ ALGORITHMIC score (không dùng AI)
        if not ai_inference_success or not verdict:
            top_green = green_flags[0] if green_flags else None
            top_red = red_flags[0] if red_flags else None

            if score >= 70:
                verdict = (
                    f"${ticker}: Lower risk than average for a meme coin based on current on-chain + market signals. "
                    f"Positive signal: {top_green or 'none detected'}. "
                    f"Main risk: {top_red or 'none detected'}. "
                    "Not financial advice. Meme coins are high-volatility; you can lose 100%."
                )
            elif score >= 50:
                verdict = (
                    f"${ticker}: Mixed signals. Positive signal: {top_green or 'none detected'}. "
                    f"Main risk: {top_red or 'none detected'}. "
                    "If you are new, consider waiting and researching more. "
                    "Not financial advice. Meme coins are high-volatility; you can lose 100%."
                )
            elif score >= 35:
                verdict = (
                    f"${ticker}: High risk. Main risk: {top_red or 'multiple risk factors'}. "
                    "If you are new, avoid or only risk money you can afford to lose. "
                    "Not financial advice. Meme coins are high-volatility; you can lose 100%."
                )
            else:
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
        
        data_sources = {
            "chain": chain_type,
            "birdeye": {"used": bool(birdeye_used), "holder_count": birdeye_data.get("holder_count"),
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
            "trust_score": score,
            "risk_level": overall["risk"],
            "scores": scores,
            "breakdown": breakdown,
            "market_data": market_data,
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
                f"Scoring: v3.1 (Technical 65% / On-Chain 35%) - Chain: {chain_type.upper()}",
                "Social: DISABLED",
                f"AI: {'✅ Verified by OpenGradient (TEE + settlement hash)' if ai_inference_success and tx_hash else ('⚠️ OpenGradient inference ok but settlement tx not verifiable yet' if ai_inference_success else '⚠️ Algorithmic Only')}",
                f"Data: DexScreener={'✅' if dex_data else '❌'}"
            ],
            "data_sources": data_sources,
        }
        
        logger.info(f"✅ Done {ticker} in {duration_ms}ms | score={score} risk={overall['risk']}")
        
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
            event_name, data = await queue.get()
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


def _calc_overall_v31(
    momentum: int, volume: int, trade: int, liquidity: int,
    holder: int, security: int, whale: int,
    market_cap: float,
    token_info: dict, holder_data: dict, liq_usd: float, age_hours: float | None, holder_count: int
) -> dict:
    """
    Overall score v3.1: Technical 65% / On-Chain 35% / Social 0%
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
