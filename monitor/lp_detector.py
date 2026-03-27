# monitor/lp_detector.py — Detect new LP additions across Solana DEXes
#
# ══ CƠ CHẾ TIẾT KIỆM API ══
#
# Vấn đề: ~500-1000 LP mới/ngày trên Raydium, mỗi coin cần 4-5 API calls
# Giải pháp: Filter pipeline sớm, mỗi stage loại bớt coin → chỉ coin "tốt" mới gọi API tốn kém
#
# Pipeline:
#   T0: Helius detect LP → FREE (đã poll sẵn)
#       Filter ngay: chỉ giữ coin có liq > $2k trên DexScreener (1 free call)
#       Bỏ: coin liq < $2k, duplicate, known stablecoins
#       ~500/ngày → ~50-80 qua T0
#
#   T1: On-chain check mintAuth/freezeAuth → 1 Solana RPC call (free/cheap)
#       Bỏ: coin còn mintAuth active (rug risk)
#       ~50 → ~25-35 qua T1
#
#   T2: DexScreener metrics (mcap/liq/holders) → 0 extra calls (dùng data từ T0)
#       Bỏ: mcap < $10k, liq < $5k
#       ~30 → ~15-20 qua T2
#
#   T3: Smart money check → 1 Birdeye call (TỐN CREDITS)
#       Chỉ gọi cho coin đã pass T0+T1+T2
#       ~15 Birdeye calls/ngày
#
#   T4: Birdeye deep scan → 1 Birdeye call (TỐN CREDITS)
#       Chỉ gọi nếu T3 pass
#       ~10 Birdeye calls/ngày
#
# Tổng Birdeye calls: ~25/ngày (tiết kiệm >95% so với scan tất cả)
#
# ══ THỜI GIAN KHỞI TẠO ══
# lp_added_at = DexScreener `pairCreatedAt` (chính xác nhất)
#             → fallback: Helius transaction timestamp
#             → đây là lúc coin CÓ THANH KHOẢN, không phải lúc mint on pump.fun

import asyncio
import time
import json
import logging
import httpx

from config import (
    HELIUS_API_KEY, BIRDEYE_API_KEY, PUMPFUN_API_KEY,
    MIN_LIQ_T0, MIN_MCAP_T2, MIN_LIQ_T2, MIN_HOLDERS_T2, MAX_TOP10_PCT,
    MAX_BIRDEYE_CALLS_PER_DAY, SCORE_MOON, PASS_COUNT_MIN,
    PENDING_AUTO_PROMOTE_LIQ,
)
from database.engine import SessionLocal
from database.models import WatchedCoin
from database.crud import log_event

logger = logging.getLogger(__name__)

# ── DEX Program IDs ──────────────────────────────────────────────
DEX_PROGRAMS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CP",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc":  "Orca",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo":  "Meteora",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora",
}

HELIUS_SOURCE_MAP = {
    "raydium": "Raydium", "orca": "Orca", "meteora": "Meteora",
    "pump_fun": "Pump.fun", "pumpfun": "Pump.fun", "pump.fun": "Pump.fun",
}

DEXSCREENER_DEX_MAP = {
    "raydium": "Raydium", "orca": "Orca", "meteora": "Meteora",
    "fluxbeam": "FluxBeam", "lifinity": "Lifinity",
}

SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj",
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
}

PUMPFUN_API = "https://frontend-api-v3.pump.fun"

_seen_sigs: set[str] = set()
MAX_SEEN = 3000

# Retry queue for coins with liq=0 (DexScreener not indexed yet)
_pending_retry: dict[str, dict] = {}
MAX_RETRY = 4
RETRY_DELAY = 45  # seconds

# Rate limit tracking for Birdeye
_birdeye_calls_today = 0
_birdeye_reset_day = 0


def _check_birdeye_budget() -> bool:
    """Check if we have Birdeye API budget left today."""
    global _birdeye_calls_today, _birdeye_reset_day
    today = int(time.time()) // 86400
    if today != _birdeye_reset_day:
        _birdeye_calls_today = 0
        _birdeye_reset_day = today
    return _birdeye_calls_today < MAX_BIRDEYE_CALLS_PER_DAY


def _use_birdeye_call():
    global _birdeye_calls_today
    _birdeye_calls_today += 1


def _identify_dex(tx: dict) -> str:
    """Identify DEX from Helius parsed transaction."""
    source = (tx.get("source") or "").lower()
    for key, name in HELIUS_SOURCE_MAP.items():
        if key in source:
            return name
    desc = (tx.get("description") or "").lower()
    for key, name in HELIUS_SOURCE_MAP.items():
        if key in desc:
            return name
    for ix in tx.get("instructions", []):
        pid = ix.get("programId", "")
        if pid in DEX_PROGRAMS:
            return DEX_PROGRAMS[pid]
    return "Unknown"


async def _pumpfun_lookup(mint: str) -> dict | None:
    """Verify token is from pump.fun. Returns None if API fails, dict otherwise."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{PUMPFUN_API}/coins/{mint}")
            if resp.status_code != 200:
                return None
            d = resp.json()
            created_ts = d.get("created_timestamp")
            complete = d.get("complete", False)
            raydium_pool = d.get("raydium_pool")
            return {
                "is_pumpfun": complete and bool(raydium_pool),
                "complete": complete,
                "created_timestamp": int(created_ts / 1000) if created_ts and created_ts > 1e12 else created_ts,
                "creator": d.get("creator"),
                "symbol": d.get("symbol", ""),
                "name": d.get("name", ""),
            }
        except Exception as e:
            logger.debug(f"Pump.fun API failed for {mint[:12]}...: {e}")
            return None


# ── Creator blacklist (in-memory cache) ──────────────────────────
_creator_blacklist: dict[str, int] = {}  # creator → rug_count


async def _check_creator(creator: str | None) -> bool:
    """Check if creator has history of rug pulls on pump.fun."""
    if not creator:
        return True  # unknown creator → pass

    # Cache hit
    if _creator_blacklist.get(creator, 0) >= 2:
        logger.info(f"  CREATOR BLACKLIST: {creator[:8]}... ({_creator_blacklist[creator]} rugs)")
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{PUMPFUN_API}/coins/user-created-coins/{creator}?limit=50")
            if resp.status_code != 200:
                return True  # API fail → don't block
            coins = resp.json()
            if not isinstance(coins, list):
                return True

            # Count dead coins: complete=False or no raydium_pool → never graduated → likely rug
            dead = sum(1 for c in coins if not c.get("complete", False))
            total = len(coins)

            if total >= 5 and dead / total > 0.8:
                _creator_blacklist[creator] = dead
                logger.info(f"  CREATOR BLACKLIST: {creator[:8]}... created {total} coins, {dead} dead")
                return False

            if dead >= 10:
                _creator_blacklist[creator] = dead
                logger.info(f"  CREATOR BLACKLIST: {creator[:8]}... {dead} dead coins")
                return False

        except Exception as e:
            logger.debug(f"Creator check failed for {creator[:8]}...: {e}")

    return True


def calculate_score(pass_count: int, holders: int, mcap: float,
                    smart_money_count: int = 0, migration_sec: int | None = None) -> int:
    """
    Shared score formula — dùng chung cho lp_detector, health_checker, coin_adder.

    Components:
      - Filter pass:     pass_count * 16        (max 80)
      - Holder velocity: min(holders, 1000) / 100  (max 10)
      - MCap signal:     min(mcap, 1M) / 100k     (max 10)
    """
    s = pass_count * 16
    s += min(10, int(min(holders, 1000) / 100))
    s += min(10, int(min(mcap, 1_000_000) / 100_000))
    return max(5, min(100, s))


async def _get_token_creation_time(mint: str) -> int | None:
    """
    Lấy thời điểm token được tạo trên blockchain (pump.fun hoặc bất kỳ).

    Dùng 2 phương pháp, ưu tiên nhanh nhất:
      1. Helius DAS API `getAsset` → trả `created_at` trực tiếp (1 call, nhanh)
      2. Fallback: Helius token creation endpoint `/v0/token-metadata`

    Cost: 1 Helius API call (included in free tier).
    """
    if not HELIUS_API_KEY:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        # Method 1: Helius DAS API — getAsset
        # Returns asset metadata including creation timestamp
        try:
            rpc_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAsset",
                "params": {"id": mint},
            }
            resp = await client.post(rpc_url, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result", {})

                # DAS returns token_info.mint_authority, supply, etc.
                # and content.metadata for creation info
                # Check multiple possible timestamp fields:

                # 1. created_at in token_info (unix timestamp)
                token_info = result.get("token_info", {})
                if token_info.get("mint_authority") is not None or token_info.get("supply"):
                    # Token exists via DAS — check for creation timestamp
                    pass

                # 2. Slot-based: get the slot and convert to timestamp
                # result.slot is the slot when getAsset was called, not creation
                # But some DAS responses include `created_at`
                created = result.get("created_at")
                if created:
                    # Could be ISO string or unix timestamp
                    if isinstance(created, (int, float)) and created > 1e9:
                        return int(created)
                    if isinstance(created, str):
                        from datetime import datetime
                        try:
                            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            return int(dt.timestamp())
                        except ValueError:
                            pass

        except Exception as e:
            logger.debug(f"DAS getAsset failed for {mint[:12]}...: {e}")

        # Method 2: Helius parsed transaction history — get oldest tx
        # More reliable but slightly heavier
        try:
            url = f"https://api.helius.xyz/v0/addresses/{mint}/transactions?api-key={HELIUS_API_KEY}&limit=1&type=CREATE"
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                txns = resp.json()
                if isinstance(txns, list) and txns:
                    ts = txns[0].get("timestamp")
                    if ts:
                        return int(ts)
        except Exception as e:
            logger.debug(f"Helius tx history failed for {mint[:12]}...: {e}")

        # Method 3: Last resort — getSignaturesForAddress, take oldest
        try:
            from config import ALCHEMY_RPC_URL
            rpc_url = ALCHEMY_RPC_URL or f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [mint, {"limit": 100}],
            }
            resp = await client.post(rpc_url, json=payload, timeout=10)
            if resp.status_code == 200:
                sigs = resp.json().get("result", [])
                if sigs:
                    oldest = sigs[-1]
                    bt = oldest.get("blockTime")
                    if bt:
                        return int(bt)
        except Exception as e:
            logger.debug(f"getSignatures failed for {mint[:12]}...: {e}")

    return None


async def _helius_get_txns(program_id: str, limit: int = 10) -> list[dict]:
    """Fetch recent parsed transactions from Helius."""
    if not HELIUS_API_KEY:
        return []
    url = f"https://api.helius.xyz/v0/addresses/{program_id}/transactions?api-key={HELIUS_API_KEY}&limit={limit}"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
            logger.debug(f"Helius {resp.status_code} for {program_id[:12]}...")
        except Exception as e:
            logger.warning(f"Helius error: {e}")
    return []


def _extract_mints_from_tx(tx: dict) -> list[dict]:
    """Extract candidate token mints from a Helius parsed transaction."""
    sig = tx.get("signature", "")
    if sig in _seen_sigs:
        return []
    _seen_sigs.add(sig)
    if len(_seen_sigs) > MAX_SEEN:
        recent = list(_seen_sigs)[-MAX_SEEN // 2:]
        _seen_sigs.clear()
        _seen_sigs.update(recent)

    desc = (tx.get("description") or "").lower()
    source = (tx.get("source") or "").lower()

    # Only process pool creation / LP events
    is_relevant = any(kw in desc for kw in [
        "initialize", "create pool", "create_pool", "add liquidity",
        "open position", "create position", "deposit", "migrat", "graduat",
    ]) or any(kw in source for kw in ["raydium", "orca", "meteora", "pump"])

    if not is_relevant:
        return []

    dex_name = _identify_dex(tx)
    results = []
    seen_mints = set()

    for transfer in tx.get("tokenTransfers", []):
        mint = transfer.get("mint", "")
        if mint and mint not in SKIP_MINTS and len(mint) >= 32 and mint not in seen_mints:
            seen_mints.add(mint)
            results.append({
                "mint": mint,
                "sig": sig,
                "timestamp": tx.get("timestamp", int(time.time())),
                "dex": dex_name,
            })

    return results


async def _dexscreener_lookup(mint: str) -> dict | None:
    """
    T0 + T2 data: DexScreener lookup (FREE, no API key needed).
    Returns pair data including dexId, liquidity, mcap, pairCreatedAt.
    """
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
                if pairs:
                    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0))
        except Exception:
            pass
    return None


async def _solana_rpc_check(mint: str) -> dict:
    """T1: On-chain mintAuthority check (1 Solana RPC call)."""
    from scrapers.solana_scraper import SolanaScraper
    scraper = SolanaScraper()
    try:
        info = await scraper.get_token_info(mint)
        if not info:
            return {"pass": False, "mint_renounced": False}
        return {
            "pass": info.get("is_mint_renounced", False),
            "mint_renounced": info.get("is_mint_renounced", False),
            "freeze_renounced": info.get("freeze_authority") is None,
        }
    except Exception as e:
        logger.warning(f"RPC check failed for {mint[:12]}...: {e}")
        return {"pass": False, "mint_renounced": False}


async def process_candidate(mint: str, dex_name: str = "Unknown", helius_ts: int = None):
    """
    Full tiered pipeline for a candidate mint.
    Exits early at each stage to save API calls.
    """
    db = SessionLocal()
    try:
        # Already watching?
        if db.query(WatchedCoin).filter(WatchedCoin.mint == mint).first():
            return

        now = int(time.time())

        # ── T0: DexScreener quick filter (FREE) ──────────────────
        dex_pair = await _dexscreener_lookup(mint)

        symbol = "???"
        name = ""
        mcap = 0
        liq = 0
        holders = 0
        lp_created_at = helius_ts or now

        if dex_pair:
            base = dex_pair.get("baseToken", {})
            symbol = base.get("symbol", "???")
            name = base.get("name", "")
            mcap = dex_pair.get("fdv", 0) or dex_pair.get("marketCap", 0) or 0
            liq_data = dex_pair.get("liquidity", {})
            liq = liq_data.get("usd", 0) if isinstance(liq_data, dict) else 0
            # pairCreatedAt is in milliseconds
            pair_ts = dex_pair.get("pairCreatedAt", 0)
            if pair_ts and pair_ts > 1e12:
                lp_created_at = int(pair_ts / 1000)
            elif pair_ts:
                lp_created_at = int(pair_ts)
            # DexScreener dexId is the most accurate DEX identification
            ds_dex = (dex_pair.get("dexId") or "").lower()
            if ds_dex in DEXSCREENER_DEX_MAP:
                dex_name = DEXSCREENER_DEX_MAP[ds_dex]
            elif ds_dex:
                dex_name = ds_dex.title()

        # T0 GATE: liq=0 → DexScreener chưa index, retry thay vì miss
        if liq < MIN_LIQ_T0:
            if liq == 0:
                attempts = _pending_retry.get(mint, {}).get("attempts", 0)
                if attempts < MAX_RETRY:
                    _pending_retry[mint] = {
                        "dex": dex_name, "helius_ts": helius_ts,
                        "attempts": attempts + 1,
                        "retry_at": int(time.time()) + RETRY_DELAY * (attempts + 1),
                    }
                    logger.debug(f"  T0 RETRY {symbol} — liq=0, attempt {attempts + 1}/{MAX_RETRY}")
                    return
            logger.debug(f"  T0 SKIP {symbol} — liq=${liq:,.0f} < ${MIN_LIQ_T0}")
            return

        # Clear retry if coin passed
        _pending_retry.pop(mint, None)

        # ── Verify pump.fun origin (1 API call) ──────────────
        pf = await _pumpfun_lookup(mint)
        if not pf or not pf["is_pumpfun"]:
            logger.debug(f"  T0 SKIP {symbol} — not pump.fun origin")
            return

        # Check creator history (1 pump.fun API call)
        creator_ok = await _check_creator(pf.get("creator"))
        if not creator_ok:
            logger.info(f"  T0 SKIP {symbol} — creator blacklisted")
            return

        t0 = True
        pump_created_at = pf["created_timestamp"]

        # Reject very old coins (>48h)
        if pump_created_at and (now - pump_created_at) > 48 * 3600:
            logger.debug(f"  T0 SKIP {symbol} — too old ({(now - pump_created_at) // 3600}h)")
            return

        # Prefer pump.fun name/symbol over DexScreener
        if pf["symbol"]:
            symbol = pf["symbol"]
        if pf["name"]:
            name = pf["name"]

        if pump_created_at and lp_created_at:
            migration_time = lp_created_at - pump_created_at
            logger.info(
                f"  T0 PASS {symbol} ({mint[:8]}...) on {dex_name} — liq=${liq:,.0f}"
                f" | pump.fun: {migration_time // 60}m ago → DEX"
            )
        else:
            logger.info(f"  T0 PASS {symbol} ({mint[:8]}...) on {dex_name} — liq=${liq:,.0f}")

        # ── T1: On-chain check (1 Solana RPC call) ───────────────
        t1_result = await _solana_rpc_check(mint)
        t1 = t1_result["pass"]
        if not t1:
            logger.info(f"  T1 FAIL {symbol} — mintAuth active")
            # Still save as degraded — might be useful to track
            _save_coin(db, mint, symbol, name, dex_name, "degraded",
                       mcap, liq, holders, pump_created_at, lp_created_at, now,
                       t0=True, t1=False, t2=False, t3=False, t4=False,
                       birdeye_checked=False, smart_money=None, degraded_at=now)
            return

        # ── T2: Market metrics (no extra call, reuse T0 data) ────
        t2 = mcap >= MIN_MCAP_T2 and liq >= MIN_LIQ_T2
        if not t2:
            logger.info(f"  T2 FAIL {symbol} — mcap=${mcap:,.0f}, liq=${liq:,.0f}")
            _save_coin(db, mint, symbol, name, dex_name, "degraded",
                       mcap, liq, holders, pump_created_at, lp_created_at, now,
                       t0=True, t1=True, t2=False, t3=False, t4=False,
                       birdeye_checked=False, smart_money=None, degraded_at=now)
            return

        # ── T3: Smart money check (1 Birdeye call — GATED) ───────
        smart_money_wallets = []
        t3 = False

        if BIRDEYE_API_KEY and _check_birdeye_budget():
            try:
                from scrapers.birdeye_client import create_birdeye_client
                be = create_birdeye_client()

                # Also get holder count while we're calling Birdeye
                overview = await be.get_token_overview(mint)
                _use_birdeye_call()
                if overview:
                    holders = overview.get("holder", 0) or 0
                    if mcap == 0:
                        mcap = overview.get("mc", 0) or overview.get("marketCap", 0) or 0

                sm_data = await be.get_smart_money_stats(mint)
                _use_birdeye_call()
                if sm_data:
                    sm_items = sm_data.get("wallets", []) or sm_data.get("items", [])
                    if isinstance(sm_items, list):
                        smart_money_wallets = [
                            w.get("address", w) if isinstance(w, dict) else str(w)
                            for w in sm_items[:10]
                        ]
                t3 = True  # Birdeye API responded
            except Exception as e:
                logger.warning(f"  T3 Birdeye failed for {symbol}: {e}")
        else:
            if not BIRDEYE_API_KEY:
                t3 = True  # Skip T3 if no key (don't penalize)
            else:
                logger.info(f"  T3 SKIP {symbol} — Birdeye daily budget exhausted ({_birdeye_calls_today}/{MAX_BIRDEYE_CALLS_PER_DAY})")
                t3 = False  # Budget exhausted → NOT pass (don't inflate score)

        # ── T4: Birdeye deep scan (1 Birdeye call — only if T3 pass) ──
        t4 = False
        birdeye_checked = False

        if t3 and BIRDEYE_API_KEY and _check_birdeye_budget():
            try:
                from scrapers.birdeye_client import create_birdeye_client
                be = create_birdeye_client()
                security = await be.get_token_security(mint)
                _use_birdeye_call()
                birdeye_checked = True
                if security:
                    top10 = security.get("top10HolderPercent", 100)
                    t4 = top10 < MAX_TOP10_PCT
                else:
                    t4 = True
            except Exception as e:
                logger.warning(f"  T4 Birdeye failed for {symbol}: {e}")
        elif not t3:
            logger.info(f"  T4 SKIP {symbol} — T3 failed")

        # ── Score + Save ──────────────────────────────────────────
        pass_count = sum([t0, t1, t2, t3, t4])
        score = calculate_score(pass_count, holders, mcap)
        status = "active" if pass_count >= PASS_COUNT_MIN else "degraded"

        _save_coin(db, mint, symbol, name, dex_name, status,
                   mcap, liq, holders, pump_created_at, lp_created_at, now,
                   t0=t0, t1=t1, t2=t2, t3=t3, t4=t4,
                   birdeye_checked=birdeye_checked,
                   smart_money=smart_money_wallets,
                   degraded_at=now if status == "degraded" else None,
                   score=score)

        logger.info(
            f"  ✅ {symbol} on {dex_name} | score={score} pass={pass_count}/5 "
            f"| mcap=${mcap:,.0f} liq=${liq:,.0f} holders={holders}"
            f" | Birdeye budget: {_birdeye_calls_today}/{MAX_BIRDEYE_CALLS_PER_DAY}"
        )

        # ── Telegram Alert ────────────────────────────────────────
        from monitor.telegram_alert import alert_new_coin, alert_signal
        await alert_new_coin(
            symbol=symbol, mint=mint, dex=dex_name, score=score,
            mcap=mcap, liq=liq, holders=holders,
            pump_created_at=pump_created_at, lp_added_at=lp_created_at,
            smart_money=smart_money_wallets, pass_count=pass_count,
            t0=t0, t1=t1, t2=t2, t3=t3, t4=t4,
        )
        # Extra signal alerts
        if score >= SCORE_MOON and status == "active":
            await alert_signal(symbol, mint, "moon",
                f"Score {score} + all filters pass on {dex_name}", score)
        if smart_money_wallets:
            await alert_signal(symbol, mint, "smart_money_in",
                f"{len(smart_money_wallets)} SM wallets detected", score)

    except Exception as e:
        logger.error(f"Error processing {mint[:12]}...: {e}")
        db.rollback()
    finally:
        db.close()


def _save_coin(db, mint, symbol, name, dex_name, status,
               mcap, liq, holders, pump_created_at, lp_created_at, now,
               t0, t1, t2, t3, t4, birdeye_checked, smart_money,
               degraded_at=None, score=None):
    """Save coin to DB + log events."""
    if score is None:
        pass_count = sum([t0, t1, t2, t3, t4])
        score = calculate_score(pass_count, holders, mcap)

    coin = WatchedCoin(
        mint=mint, symbol=symbol, name=name, dex=dex_name,
        status=status, score=score, mcap=mcap, liq=liq, holders=holders,
        smart_money=json.dumps(smart_money) if smart_money else None,
        pump_created_at=pump_created_at,
        lp_added_at=lp_created_at, last_checked=now,
        degraded_at=degraded_at,
        filter_t0=t0, filter_t1=t1, filter_t2=t2, filter_t3=t3, filter_t4=t4,
        birdeye_checked=birdeye_checked,
    )
    db.add(coin)
    db.commit()

    # Log events
    log_event(db, mint, symbol, "new", f"LP add detect — {dex_name}", score=score)

    if status == "active":
        pass_count = sum([t0, t1, t2, t3, t4])
        log_event(db, mint, symbol, "ok", f"Pass {pass_count}/5 criteria. Score: {score}", score=score)

    if smart_money:
        for w in smart_money[:3]:
            log_event(db, mint, symbol, "sm", f"{w[:8]}... smart money detected", score=score)

    if status == "degraded":
        reasons = []
        if not t1: reasons.append("mintAuth active")
        if not t2: reasons.append(f"mcap=${mcap:,.0f} liq=${liq:,.0f}")
        log_event(db, mint, symbol, "warn", f"Degraded — {', '.join(reasons) or 'low score'}", score=score)


# save_new_token removed — pump.fun creates ~1000 coins/min, saving all would flood DB.
# Only migrate events (graduated coins) are tracked via process_candidate_light().


async def poll_pumpfun_graduates(max_add: int = 30):
    """
    Poll pump.fun API for top graduated coins sorted by market_cap.
    No age filter — graduated coins are valuable regardless of when created.
    Only loads coins with mcap > $5k to filter dead ones.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            headers = {}
            if PUMPFUN_API_KEY:
                headers["x-api-key"] = PUMPFUN_API_KEY

            count = 0
            db = SessionLocal()
            try:
                for offset in [0, 50, 100]:
                    if count >= max_add:
                        break
                    resp = await client.get(
                        f"{PUMPFUN_API}/coins?offset={offset}&limit=50"
                        f"&sort=market_cap&order=DESC&includeNsfw=false",
                        headers=headers, timeout=10,
                    )
                    if resp.status_code != 200:
                        break
                    coins = resp.json()
                    if not isinstance(coins, list) or not coins:
                        break

                    for c in coins:
                        if count >= max_add:
                            break
                        mint = c.get("mint")
                        if not mint:
                            continue
                        # Only graduated coins with Raydium pool
                        if not c.get("complete") or not c.get("raydium_pool"):
                            continue
                        # Require minimum mcap to filter dead coins
                        usd_mcap = c.get("usd_market_cap", 0) or 0
                        if usd_mcap < 5_000:
                            continue
                        # Skip if already in DB
                        if db.query(WatchedCoin).filter(WatchedCoin.mint == mint).first():
                            continue
                        await process_candidate_light(mint, "Raydium")
                        count += 1
                        await asyncio.sleep(0.3)

                    await asyncio.sleep(0.3)
            finally:
                db.close()

            if count:
                logger.info(f"  pump.fun poll: {count} graduates loaded")
            return count
        except Exception as e:
            logger.debug(f"poll_pumpfun_graduates error: {e}")
            return 0


async def process_candidate_light(mint: str, dex_name: str = "Unknown", helius_ts: int = None):
    """
    Tier 1 — Light scan: 0 paid API calls.
    Creates a "pending" entry for every pump.fun graduate.
    Only uses pump.fun API (free) + DexScreener (free).
    """
    db = SessionLocal()
    try:
        if db.query(WatchedCoin).filter(WatchedCoin.mint == mint).first():
            return

        now = int(time.time())

        # Verify pump.fun origin (free)
        pf = await _pumpfun_lookup(mint)
        if not pf or not pf["is_pumpfun"]:
            return

        # Creator blacklist (free)
        creator_ok = await _check_creator(pf.get("creator"))
        if not creator_ok:
            logger.info(f"  LIGHT SKIP {pf.get('symbol', '?')} — creator blacklisted")
            return

        symbol = pf["symbol"] or "???"
        name = pf["name"] or ""
        pump_created_at = pf["created_timestamp"]

        # DexScreener lookup (free) — ok if returns nothing
        dex_pair = await _dexscreener_lookup(mint)
        mcap = 0
        liq = 0
        lp_created_at = helius_ts or now

        if dex_pair:
            base = dex_pair.get("baseToken", {})
            if not symbol or symbol == "???":
                symbol = base.get("symbol", symbol)
            if not name:
                name = base.get("name", name)
            mcap = dex_pair.get("fdv", 0) or dex_pair.get("marketCap", 0) or 0
            liq_data = dex_pair.get("liquidity", {})
            liq = liq_data.get("usd", 0) if isinstance(liq_data, dict) else 0
            pair_ts = dex_pair.get("pairCreatedAt", 0)
            if pair_ts and pair_ts > 1e12:
                lp_created_at = int(pair_ts / 1000)
            elif pair_ts:
                lp_created_at = int(pair_ts)
            ds_dex = (dex_pair.get("dexId") or "").lower()
            if ds_dex in DEXSCREENER_DEX_MAP:
                dex_name = DEXSCREENER_DEX_MAP[ds_dex]

        score = calculate_score(1, 0, mcap)  # only T0 pass

        coin = WatchedCoin(
            mint=mint, symbol=symbol, name=name, dex=dex_name,
            status="pending", score=score, mcap=mcap, liq=liq, holders=0,
            pump_created_at=pump_created_at, lp_added_at=lp_created_at,
            last_checked=now, filter_t0=True,
            filter_t1=False, filter_t2=False, filter_t3=False, filter_t4=False,
            birdeye_checked=False, deep_scanned=False,
        )
        db.add(coin)
        db.commit()

        log_event(db, mint, symbol, "new",
                  f"pump.fun → {dex_name} | MCap {'${:,.0f}'.format(mcap) if mcap else '$0'} | Liq {'${:,.0f}'.format(liq) if liq else '$0'} | pending",
                  score=score)

        logger.info(f"  ⚡ LIGHT {symbol} ({mint[:8]}...) on {dex_name} | mcap=${mcap:,.0f} liq=${liq:,.0f} | pending")

        # Telegram alert (light — no need for full pipeline info)
        from monitor.telegram_alert import send_message
        await send_message(
            f"⚡ <b>NEW: ${symbol}</b> (pending)\n"
            f"🏦 {dex_name} | MCap {'${:,.0f}'.format(mcap) if mcap else '$0'} | Liq {'${:,.0f}'.format(liq) if liq else '$0'}\n"
            f"🔗 <code>{mint}</code>\n"
            f"📈 <a href=\"https://dexscreener.com/solana/{mint}\">DexScreener</a>"
        )

    except Exception as e:
        logger.error(f"Light scan error {mint[:12]}...: {e}")
        db.rollback()
    finally:
        db.close()


async def run_deep_scan(mint: str) -> dict:
    """
    Tier 2 — Deep scan: runs T1-T4 on an existing pending/degraded coin.
    Called on-demand (user click) or auto-promote by health_checker.
    Returns updated coin data dict.
    """
    db = SessionLocal()
    try:
        coin = db.query(WatchedCoin).filter(WatchedCoin.mint == mint).first()
        if not coin:
            return {"ok": False, "error": "Coin not found"}
        if coin.deep_scanned:
            return {"ok": True, "already": True, "symbol": coin.symbol, "status": coin.status, "score": coin.score}

        now = int(time.time())
        symbol = coin.symbol
        mcap = coin.mcap or 0
        liq = coin.liq or 0
        holders = coin.holders or 0
        dex_name = coin.dex or "Unknown"

        # Refresh DexScreener data
        dex_pair = await _dexscreener_lookup(mint)
        if dex_pair:
            mcap = dex_pair.get("fdv", 0) or dex_pair.get("marketCap", 0) or mcap
            liq_data = dex_pair.get("liquidity", {})
            liq = liq_data.get("usd", 0) if isinstance(liq_data, dict) else liq

        # ── T1: On-chain check ──
        t1_result = await _solana_rpc_check(mint)
        t1 = t1_result["pass"]

        # ── T2: Market metrics ──
        t2 = mcap >= MIN_MCAP_T2 and liq >= MIN_LIQ_T2

        # ── T3: Smart money (Birdeye — gated) ──
        smart_money_wallets = []
        t3 = False
        if BIRDEYE_API_KEY and _check_birdeye_budget():
            try:
                from scrapers.birdeye_client import create_birdeye_client
                be = create_birdeye_client()
                overview = await be.get_token_overview(mint)
                _use_birdeye_call()
                if overview:
                    holders = overview.get("holder", 0) or 0
                    if mcap == 0:
                        mcap = overview.get("mc", 0) or overview.get("marketCap", 0) or 0
                sm_data = await be.get_smart_money_stats(mint)
                _use_birdeye_call()
                if sm_data:
                    sm_items = sm_data.get("wallets", []) or sm_data.get("items", [])
                    if isinstance(sm_items, list):
                        smart_money_wallets = [
                            w.get("address", w) if isinstance(w, dict) else str(w)
                            for w in sm_items[:10]
                        ]
                t3 = True
            except Exception as e:
                logger.warning(f"  T3 Birdeye failed for {symbol}: {e}")
        elif not BIRDEYE_API_KEY:
            t3 = True

        # ── T4: Birdeye deep scan ──
        t4 = False
        birdeye_checked = False
        if t3 and BIRDEYE_API_KEY and _check_birdeye_budget():
            try:
                from scrapers.birdeye_client import create_birdeye_client
                be = create_birdeye_client()
                security = await be.get_token_security(mint)
                _use_birdeye_call()
                birdeye_checked = True
                if security:
                    t4 = security.get("top10HolderPercent", 100) < MAX_TOP10_PCT
                else:
                    t4 = True
            except Exception as e:
                logger.warning(f"  T4 Birdeye failed for {symbol}: {e}")

        # ── Score + Update ──
        pass_count = sum([True, t1, t2, t3, t4])  # T0 always True
        score = calculate_score(pass_count, holders, mcap)
        status = "active" if pass_count >= PASS_COUNT_MIN else "degraded"

        coin.score = score
        coin.status = status
        coin.mcap = mcap
        coin.liq = liq
        coin.holders = holders
        coin.filter_t1 = t1
        coin.filter_t2 = t2
        coin.filter_t3 = t3
        coin.filter_t4 = t4
        coin.birdeye_checked = birdeye_checked
        coin.deep_scanned = True
        coin.last_checked = now
        coin.smart_money = json.dumps(smart_money_wallets) if smart_money_wallets else coin.smart_money
        if status == "degraded":
            coin.degraded_at = now
        db.commit()

        # Log events
        log_event(db, mint, symbol, "ok" if status == "active" else "warn",
                  f"Deep scan: {pass_count}/5 pass. Score: {score} → {status}",
                  score=score)
        if smart_money_wallets:
            for w in smart_money_wallets[:3]:
                log_event(db, mint, symbol, "sm", f"{w[:8]}... smart money detected", score=score)

        # Telegram alert for promoted coins
        if status == "active":
            from monitor.telegram_alert import alert_new_coin
            await alert_new_coin(
                symbol=symbol, mint=mint, dex=dex_name, score=score,
                mcap=mcap, liq=liq, holders=holders,
                pump_created_at=coin.pump_created_at, lp_added_at=coin.lp_added_at,
                smart_money=smart_money_wallets, pass_count=pass_count,
                t0=True, t1=t1, t2=t2, t3=t3, t4=t4,
            )

        logger.info(f"  🔬 DEEP {symbol} | {pass_count}/5 pass | score={score} → {status}")

        return {
            "ok": True, "symbol": symbol, "status": status, "score": score,
            "mcap": mcap, "liq": liq, "holders": holders,
            "pass_count": pass_count, "smart_money": len(smart_money_wallets),
        }

    except Exception as e:
        logger.error(f"Deep scan error {mint[:12]}...: {e}")
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


async def flush_retry_queue():
    """Process coins that had liq=0 on first check (DexScreener not indexed yet)."""
    now = int(time.time())
    due = [m for m, v in list(_pending_retry.items()) if v["retry_at"] <= now]
    for mint in due:
        info = _pending_retry.pop(mint)
        logger.info(f"  RETRY {mint[:8]}... attempt {info['attempts']}/{MAX_RETRY}")
        await process_candidate(
            mint=mint,
            dex_name=info.get("dex", "Unknown"),
            helius_ts=info.get("helius_ts"),
        )
        await asyncio.sleep(1.5)


async def scan_new_lps():
    """
    One scan cycle: poll DEX programs via Helius → filter → process.
    Only polls Raydium AMM V4 + CLMM (highest volume, most new meme coins).
    """
    if not HELIUS_API_KEY:
        logger.warning("HELIUS_API_KEY not set — LP detector disabled")
        return 0

    # Poll all major DEXes where pump.fun tokens migrate to
    programs = [
        ("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "Raydium"),     # AMM V4
        ("CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C", "Raydium CP"),   # CP/LaunchPad
        ("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  "Orca"),
        ("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",  "Meteora"),
    ]

    all_candidates = []

    for pid, label in programs:
        txns = await _helius_get_txns(pid, limit=10)
        for tx in txns:
            mints = _extract_mints_from_tx(tx)
            all_candidates.extend(mints)
        await asyncio.sleep(0.5)  # Small gap between program polls

    if not all_candidates:
        return 0

    # Deduplicate by mint
    seen = set()
    unique = []
    for c in all_candidates:
        if c["mint"] not in seen:
            seen.add(c["mint"])
            unique.append(c)

    logger.info(f"🔍 Found {len(unique)} candidates from {len(programs)} DEX programs")

    count = 0
    for candidate in unique:
        await process_candidate_light(
            mint=candidate["mint"],
            dex_name=candidate.get("dex", "Unknown"),
            helius_ts=candidate.get("timestamp"),
        )
        count += 1
        await asyncio.sleep(1.5)  # Rate limit between coins

    return count


async def lp_detector_loop(interval_seconds: int = 30):
    """Background loop: scan for new LPs every N seconds."""
    logger.info(
        f"🚀 LP Detector started (interval={interval_seconds}s)\n"
        f"   Monitoring: Raydium AMM V4, Raydium CP\n"
        f"   T0 gate: liq ≥ ${MIN_LIQ_T0} | T2 gate: mcap ≥ ${MIN_MCAP_T2}, liq ≥ ${MIN_LIQ_T2}\n"
        f"   Birdeye daily budget: {MAX_BIRDEYE_CALLS_PER_DAY} calls"
    )
    while True:
        try:
            found = await scan_new_lps()
            if found:
                logger.info(f"  Processed {found} candidates | Birdeye: {_birdeye_calls_today}/{MAX_BIRDEYE_CALLS_PER_DAY} used today")
            # Poll pump.fun API for top graduated coins
            await poll_pumpfun_graduates()
            # Flush retry queue (coins with liq=0 on first check)
            if _pending_retry:
                await flush_retry_queue()
        except Exception as e:
            logger.error(f"LP detector error: {e}")
        await asyncio.sleep(interval_seconds)
