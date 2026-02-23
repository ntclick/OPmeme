# 🎯 SOLANA TOKEN ANALYZER v3.1 — TECHNICAL FOCUS SPEC

> **Version:** 3.1  
> **Weights:** Technical 65% / On-Chain 35% / Social 0%  
> **Social:** DISABLED (sẽ enable khi Twitter scraper ổn định)  
> **Date:** 2026-02-22

---

## 📋 TASK SUMMARY

Refactor Solana Token Analyzer để:
1. **BỎ** hoàn toàn Twitter/Social scoring (tạm thời)
2. **TĂNG** Technical weight: 55% → 65%
3. **TĂNG** On-Chain weight: 30% → 35%
4. **FIX** Birdeye data extraction bugs
5. **HIỆN** override reasons trong RED FLAGS

---

## 🏗️ ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────┐
│                    TOTAL SCORE = 100                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ╔═════════════════════════════════════════════════════════╗   │
│  ║  🔧 TECHNICAL (65%)                                     ║   │
│  ║  ├── Momentum      25%  ← price changes 1h/6h/24h       ║   │
│  ║  ├── Volume        18%  ← vol/mcap, buy/sell volume     ║   │
│  ║  ├── Trade Press   12%  ← buy/sell count, unique users  ║   │
│  ║  └── Liquidity     10%  ← exit liquidity, liq/mcap      ║   │
│  ╚═════════════════════════════════════════════════════════╝   │
│                                                                 │
│  ╔═════════════════════════════════════════════════════════╗   │
│  ║  🔗 ON-CHAIN (35%)                                      ║   │
│  ║  ├── Holder        15%  ← holder count, distribution    ║   │
│  ║  ├── Security      12%  ← mint/freeze, token age        ║   │
│  ║  └── Whale Risk     8%  ← top 10 concentration          ║   │
│  ╚═════════════════════════════════════════════════════════╝   │
│                                                                 │
│  ╔═════════════════════════════════════════════════════════╗   │
│  ║  📱 SOCIAL (0%) — DISABLED                              ║   │
│  ║  └── Will enable when Twitter scraper stable            ║   │
│  ╚═════════════════════════════════════════════════════════╝   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📁 FILES TO MODIFY

```
project/
├── api/
│   ├── routes.py           ← REWRITE (main changes)
│   └── schemas.py          ← Minor updates
├── analysis/
│   ├── scoring.py          ← Can keep but routes is self-contained
│   ├── technical_analyzer.py   ← OK, no changes needed
│   ├── liquidity_analyzer.py   ← FIX signature
│   ├── bot_detector.py     ← SKIP (Social disabled)
│   └── sentiment.py        ← SKIP (Social disabled)
├── scrapers/
│   ├── birdeye_client.py   ← FIX error handling
│   ├── solana_scraper.py   ← OK
│   └── twitter_scraper.py  ← SKIP (Social disabled)
└── utils/
    └── cache.py            ← OK
```

---

## 🔄 DATA FLOW

```
User Input (Contract Address)
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│               PARALLEL DATA FETCHING                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   BIRDEYE    │  │ DEXSCREENER  │  │  SOLANA RPC  │      │
│  │  (PRIMARY)   │  │ (SECONDARY)  │  │  (FALLBACK)  │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                 │                 │               │
│         ▼                 ▼                 ▼               │
│  • holder count    • price changes   • token info          │
│  • volume 24h      • liquidity USD   • mint/freeze         │
│  • buy/sell counts • pair age        • top holders         │
│  • unique traders  • market cap                            │
│  • price changes                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                   SCORING ENGINE                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Technical (65%)              On-Chain (35%)                │
│  ├── Momentum   25%           ├── Holder    15%             │
│  ├── Volume     18%           ├── Security  12%             │
│  ├── Trade      12%           └── Whale      8%             │
│  └── Liquidity  10%                                         │
│                                                             │
│  Formula:                                                   │
│  raw_score = (M×0.25 + V×0.18 + T×0.12 + L×0.10)           │
│            + (H×0.15 + S×0.12 + W×0.08)                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                  OVERRIDE RULES                             │
├─────────────────────────────────────────────────────────────┤
│  Context-aware caps based on token maturity:                │
│                                                             │
│  is_established = holders > 10K OR age > 30 days            │
│  is_large_cap = liquidity > $1M OR holders > 50K            │
│                                                             │
│  RULE                    NEW    ESTABLISHED   LARGE_CAP     │
│  ─────────────────────────────────────────────────────────  │
│  Mint authority ACTIVE   max 35    max 55      max 70       │
│  Liquidity < $10K        max 30    max 50      N/A          │
│  Top 10 > 80%            max 30    max 50      max 65       │
│  Holders < 100           max 35    max 35      max 35       │
│  Token < 24h             max 40    N/A         max 55       │
│  Freeze authority        max 50    max 50      max 75       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                   FINAL OUTPUT                              │
├─────────────────────────────────────────────────────────────┤
│  {                                                          │
│    "overall_score": 68,                                     │
│    "risk_level": "LOW",                                     │
│    "breakdown": {                                           │
│      "technical": {"total": 44.2, "weight": "65%"},         │
│      "onchain": {"total": 23.8, "weight": "35%"},           │
│      "social": {"total": 0, "status": "DISABLED"}           │
│    },                                                       │
│    "red_flags": ["🔴 Override reason here"],                │
│    "green_flags": ["🟢 Positive signal"]                    │
│  }                                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔑 DATA SOURCES

### 1. Birdeye API (PRIMARY)

**Base URL:** `https://public-api.birdeye.so` 

**⚠️ CRITICAL HEADERS:**
```python
headers = {
    "X-API-KEY": BIRDEYE_API_KEY,
    "x-chain": "solana",      # ← REQUIRED! Missing = 400 error
    "accept": "application/json"
}
```

**Main Endpoint:** `GET /defi/token_overview?address={mint}` 

**Response Fields Used:**
```python
{
    # Market
    "price": 0.00002134,
    "liquidity": 25000000,       # USD
    "mc": 1500000000,            # Market cap
    
    # Holders (UNIQUE to Birdeye!)
    "holder": 750000,            # Total holders
    "uniqueWallet24h": 25000,    # Unique traders 24h
    
    # Volume
    "v24hUSD": 50000000,         # Volume 24h
    "vBuy24hUSD": 28000000,      # Buy volume
    "vSell24hUSD": 22000000,     # Sell volume
    
    # Trades
    "buy24h": 85000,             # Buy count
    "sell24h": 65000,            # Sell count
    "trade24h": 150000,          # Total trades
    
    # Price Changes
    "priceChange24hPercent": 15.5,
    "priceChange1hPercent": 2.1,
}
```

**Extract Function:**
```python
def extract_scores_from_overview(overview: dict) -> dict:
    return {
        "price": overview.get("price"),
        "liquidity": overview.get("liquidity"),
        "market_cap": overview.get("mc"),
        "holder_count": overview.get("holder"),
        "unique_traders_24h": overview.get("uniqueWallet24h"),
        "volume_24h": overview.get("v24hUSD"),
        "buy_volume_24h": overview.get("vBuy24hUSD"),
        "sell_volume_24h": overview.get("vSell24hUSD"),
        "trades_24h": overview.get("trade24h"),
        "buys_24h": overview.get("buy24h"),
        "sells_24h": overview.get("sell24h"),
        "price_change_1h": overview.get("priceChange1hPercent"),
        "price_change_24h": overview.get("priceChange24hPercent"),
    }
```

### 2. DexScreener API (SECONDARY)

**Endpoint:** `GET /latest/dex/tokens/{address}` 

**Used for:**
- Price changes (h1, h6, h24) - backup if Birdeye missing
- Token age (pairCreatedAt)
- Liquidity USD - backup

```python
dex_data = {
    "priceChange": {"h1": 2.1, "h6": 8.5, "h24": 15.5},
    "liquidity": {"usd": 25000000},
    "pairCreatedAt": 1700000000,  # milliseconds
    "fdv": 1500000000,
}
```

### 3. Solana RPC (FALLBACK)

**Used for:**
- Token info (mint/freeze authority)
- Top holders list

---

## 📊 SCORING MATRICES

### 1. MOMENTUM SCORE (25%)

**Input:** `{"1h": X, "6h": Y, "24h": Z}` 

**24h Price Change Scoring:**
| Change | Score | Signal |
|--------|-------|--------|
| >100% | 40 | 🔴 OVERBOUGHT |
| 50-100% | 55 | 🟡 Extended |
| 20-50% | 85 | 🟢 STRONG BULLISH |
| 5-20% | 90 | 🟢 BULLISH |
| 0-5% | 70 | 🟢 Slightly bullish |
| -5-0% | 60 | 🟡 Neutral |
| -10 to -5% | 50 | 🟡 Slightly bearish |
| -20 to -10% | 40 | 🔴 BEARISH |
| -50 to -20% | 30 | 🔴 Strong bearish |
| <-50% | 20 | 🔴 CRASH |

**1h/6h Price Change Scoring:**
| Change | Score | Signal |
|--------|-------|--------|
| >20% | 50 | 🟡 Pump in progress |
| 10-20% | 75 | 🟢 Strong momentum |
| 5-10% | 85 | 🟢 Good momentum |
| 0-5% | 70 | 🟢 Stable upward |
| -5-0% | 60 | 🟡 Consolidating |
| -10 to -5% | 45 | 🟡 Weakness |
| <-10% | 30 | 🔴 Dumping |

**Trend Consistency Bonus:**
| Pattern | Bonus | Trend |
|---------|-------|-------|
| All positive | +15 | STRONG_BULLISH |
| 24h+ but 1h- | +10 | PULLBACK |
| 24h- but 1h+ | +5 | POTENTIAL_REVERSAL |
| All negative | -10 | STRONG_BEARISH |
| 24h>50% and 1h<-10% | -20 | DUMP_AFTER_PUMP |

**Formula:**
```python
base = score_1h × 0.25 + score_6h × 0.25 + score_24h × 0.30
final = base + (trend_bonus × 0.20)
```

---

### 2. VOLUME SCORE (18%)

**Volume/MCap Ratio (40%):**
| Ratio | Score | Signal |
|-------|-------|--------|
| >100% | 95 | 🔥 EXTREME (wash?) |
| 50-100% | 90 | 🟢 Very high |
| 20-50% | 85 | 🟢 High |
| 10-20% | 75 | 🟢 Good |
| 5-10% | 60 | 🟡 Moderate |
| 2-5% | 45 | 🟡 Low |
| 1-2% | 30 | 🔴 Very low |
| <1% | 15 | 🔴 DEAD |

**Buy Volume Dominance (30%):**
| Buy % | Score | Signal |
|-------|-------|--------|
| >70% | 95 | 🟢 STRONG accumulation |
| 60-70% | 85 | 🟢 Buy pressure |
| 55-60% | 70 | 🟢 Slightly bullish |
| 45-55% | 50 | 🟡 Balanced |
| 40-45% | 35 | 🟡 Slightly bearish |
| 30-40% | 20 | 🔴 Sell pressure |
| <30% | 10 | 🔴 HEAVY distribution |

**Volume Change 24h (30%):**
| Change | Score |
|--------|-------|
| >100% | 90 |
| 50-100% | 80 |
| 20-50% | 70 |
| 0-20% | 60 |
| -20-0% | 45 |
| -50 to -20% | 30 |
| <-50% | 15 |

---

### 3. TRADE PRESSURE SCORE (12%)

**Buy/Sell Ratio (50%):**
| Ratio | Score | Signal |
|-------|-------|--------|
| >3.0 | 95 | 🟢 EXTREME buy |
| 2.0-3.0 | 90 | 🟢 Very strong |
| 1.5-2.0 | 80 | 🟢 Strong |
| 1.2-1.5 | 70 | 🟢 Moderate |
| 1.0-1.2 | 55 | 🟡 Slightly bullish |
| 0.8-1.0 | 45 | 🟡 Slightly bearish |
| 0.6-0.8 | 30 | 🔴 Sell pressure |
| 0.4-0.6 | 20 | 🔴 Strong sell |
| <0.4 | 10 | 🔴 DUMP MODE |

**Unique Traders (30%):**
| Count | Score |
|-------|-------|
| >10,000 | 100 |
| 5,000-10,000 | 90 |
| 2,000-5,000 | 80 |
| 1,000-2,000 | 70 |
| 500-1,000 | 55 |
| 200-500 | 40 |
| 100-200 | 25 |
| <100 | 10 |

**Trade Frequency - trades per user (20%):**
| TPU | Score | Note |
|-----|-------|------|
| <2 | 50 | Low activity |
| 2-5 | 70 | Normal |
| >5 | 40 | Wash trading? |

---

### 4. LIQUIDITY SCORE (10%)

**Liquidity USD (50%):**
| USD | Score | Signal |
|-----|-------|--------|
| >$5M | 100 | 🟢 Excellent |
| $1M-$5M | 90 | 🟢 Very good |
| $500K-$1M | 80 | 🟢 Good |
| $200K-$500K | 70 | 🟢 Adequate |
| $100K-$200K | 55 | 🟡 Moderate |
| $50K-$100K | 40 | 🟡 Low |
| $20K-$50K | 25 | 🔴 Very low |
| $10K-$20K | 15 | 🔴 Micro |
| <$10K | 5 | 🔴 CRITICAL |

**Liquidity/MCap Ratio (30%):**
| Ratio | Score | Signal |
|-------|-------|--------|
| >20% | 100 | 🟢 Extremely healthy |
| 10-20% | 90 | 🟢 Very healthy |
| 5-10% | 75 | 🟢 Healthy |
| 3-5% | 60 | 🟡 Acceptable |
| 1-3% | 40 | 🟡 Low |
| 0.5-1% | 25 | 🔴 Poor |
| <0.5% | 10 | 🔴 RUG RISK |

---

### 5. HOLDER SCORE (15%)

| Count | Score |
|-------|-------|
| >100,000 | 100 |
| 50,000-100,000 | 90 |
| 20,000-50,000 | 80 |
| 10,000-20,000 | 70 |
| 5,000-10,000 | 60 |
| 2,000-5,000 | 50 |
| 1,000-2,000 | 35 |
| 500-1,000 | 25 |
| 100-500 | 15 |
| <100 | 5 |

---

### 6. SECURITY SCORE (12%)

**Mint Authority (40%):**
- Renounced (null): 100
- Active: 0

**Freeze Authority (25%):**
- Renounced (null): 100
- Active: 20

**Token Age (35%):**
| Age | Score |
|-----|-------|
| >90 days | 100 |
| 30-90 days | 85 |
| 14-30 days | 70 |
| 7-14 days | 55 |
| 3-7 days | 40 |
| 1-3 days | 25 |
| <24h | 10 |

---

### 7. WHALE RISK SCORE (8%)

| Top 10 % | Score |
|----------|-------|
| <20% | 100 |
| 20-30% | 85 |
| 30-40% | 70 |
| 40-50% | 55 |
| 50-60% | 40 |
| 60-70% | 25 |
| 70-80% | 15 |
| >80% | 5 |

---

## 🎯 RISK LEVELS

| Score | Level | Color | Action |
|-------|-------|-------|--------|
| 80-100 | MOON | 🟢 | Strong buy candidate |
| 65-79 | LOW | 🟢 | Good for investment |
| 50-64 | MEDIUM | 🟡 | DYOR carefully |
| 35-49 | HIGH | 🔴 | High risk |
| 20-34 | EXTREME | 🔴 | Very dangerous |
| 0-19 | AVOID | ☠️ | Do not invest |

---

## 🔧 IMPLEMENTATION CHANGES

### routes.py Changes:

```python
# REMOVE these imports:
# from scrapers.twitter_scraper import TwitterScraper
# from analysis.bot_detector import calculate_bot_score
# from analysis.sentiment import calculate_sentiment_score

# REMOVE Twitter scraping:
# tweets = twitter_scraper.scrape_by_ticker(ticker, max_tweets=100)

# UPDATE weights in _calculate_overall_score_v31():
technical_total = (
    momentum_score * 0.25 +   # was 0.20
    volume_score * 0.18 +     # was 0.15
    trade_pressure_score * 0.12 +  # was 0.10
    liquidity_score * 0.10    # same
)

onchain_total = (
    holder_score * 0.15 +     # was 0.12
    security_score * 0.12 +   # same
    whale_risk_score * 0.08   # was 0.06
)

# Social = 0 (DISABLED)

# ADD override reasons to red_flags:
if overall_result.get("override_reasons"):
    all_red_flags.extend(overall_result["override_reasons"])
```

### liquidity_analyzer.py Fix:

```python
# CHANGE signature from:
def calculate_liquidity_score(self, contract_address, birdeye_data, holder_data):

# TO:
def calculate_liquidity_score(self, birdeye_data: dict, holder_data: dict) -> dict:
```

### birdeye_client.py Improvements:

```python
# ADD better error handling:
async def get_token_overview(self, mint: str) -> Optional[dict]:
    try:
        data = await self._request("/defi/token_overview", {"address": mint})
        if data:
            logger.info(f"✅ Birdeye: holder={data.get('holder')}, vol={data.get('v24hUSD')}")
        return data
    except Exception as e:
        logger.error(f"❌ Birdeye overview failed: {e}")
        return None
```

---

## ✅ CHECKLIST

- [ ] `api/routes.py` — Remove Twitter, update weights to 65/35
- [ ] `analysis/liquidity_analyzer.py` — Fix function signature
- [ ] `scrapers/birdeye_client.py` — Add better logging
- [ ] Test with known tokens (BONK, WIF, POPCAT)
- [ ] Verify RED FLAGS show override reasons
- [ ] Verify all component scores display (not --)

---

## 🧪 TEST COMMANDS

```bash
# Test endpoint
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"}'

# Force refresh (skip cache)
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "BONK", "force_refresh": true}'

# Clear cache
curl -X POST http://localhost:8000/api/admin/clear-cache
```

---

## 📝 EXPECTED RESPONSE

```json
{
  "ticker": "BONK",
  "overall_score": 72,
  "risk_level": "LOW",
  "breakdown": {
    "technical": {
      "total": 46.8,
      "weight": "65%",
      "components": {
        "momentum": {"score": 75, "weight": "25%"},
        "volume": {"score": 82, "weight": "18%"},
        "trade_pressure": {"score": 70, "weight": "12%"},
        "liquidity": {"score": 90, "weight": "10%"}
      }
    },
    "onchain": {
      "total": 25.2,
      "weight": "35%",
      "components": {
        "holder": {"score": 85, "weight": "15%"},
        "security": {"score": 70, "weight": "12%"},
        "whale_risk": {"score": 65, "weight": "8%"}
      }
    },
    "social": {
      "total": 0,
      "weight": "0%",
      "status": "DISABLED"
    }
  },
  "red_flags": [],
  "green_flags": [
    "🟢 Strong bullish trend",
    "🟢 Buy pressure dominant (68%)",
    "🟢 Good liquidity ($2,500,000)",
    "🟢 Mint authority renounced ✓",
    "🟢 Large holder base (750,000)"
  ],
  "tweets_analyzed": 0,
  "algorithm_notes": [
    "Scoring: v3.1 (Technical 65% / On-Chain 35%)",
    "Social analysis: DISABLED"
  ]
}
```

---

*Spec v3.1 — Technical Focus — Social Disabled — 2026-02-22*
