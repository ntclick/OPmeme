# 🐛 Bug Fix Summary — Component Scores Showing "--"

## Problem
UI hiển thị tất cả component scores là "--" (null) mặc dù Trust Score = 68

```
┌─────────────────────────────────────────────────────────────┐
│  TECHNICAL (55%)          ON-CHAIN (30%)      SOCIAL (15%) │
│  --/100                   --/100              --/100       │
│                                                            │
│  Momentum (20%)  --       Holder (12%)  --    Community -- │
│  Volume (15%)    --       Security (12%) --   Bot Detect-- │
│  Trade (10%)     --       Whale (6%)    --                 │
│  Liquidity (10%) --                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Root Causes

### Bug 1: Response `scores` field không match schema

**routes.py (OLD - WRONG):**
```python
"scores": {
    "overall": overall_result["overall_score"],
    "risk_level": overall_result["risk_level"],
    "breakdown": overall_result.get("breakdown", {}),
},
```

**schemas.py expects `ScoreBreakdown`:**
```python
class ScoreBreakdown(BaseModel):
    social_score: int
    bot_score: int
    sentiment_score: int
    holder_score: int
    momentum_score: Optional[int]
    volume_score: Optional[int]
    liquidity_score: Optional[int]
    security_score: Optional[int]
    ai_trust_score: Optional[int]
```

→ **Mismatch!** Frontend expect `scores.momentum_score`, nhận được `scores.overall`

---

### Bug 2: technical_analyzer function signature mismatch

**routes.py gọi:**
```python
momentum_result = await tech_analyzer.calculate_momentum_score(contract_address, birdeye_data)
```

**technical_analyzer.py expect:**
```python
def calculate_momentum_score(self, price_changes: dict) -> dict:
    # expects {"1h": 5.2, "6h": -2.1, "24h": 15.5}
```

→ Passing wrong parameters → Function fails silently → Returns default 50

---

### Bug 3: Missing scores không được tính

- `trade_pressure_score` - không được gọi
- `whale_risk_score` - không được tính
- `extended_breakdown` - không được build

---

## Fixes Applied

### Fix 1: Build `scores` correctly

```python
# NEW - CORRECT
score_breakdown = ScoreBreakdown(
    social_score=social_result["social_score"],
    bot_score=bot_result["bot_score"],
    sentiment_score=sentiment_final,
    holder_score=holder_result["holder_score"],
    ai_trust_score=ai_trust,
    liquidity_score=liquidity_result.get("liquidity_score"),
    security_score=security_result.get("security_score"),
    momentum_score=momentum_result.get("momentum_score"),
    volume_score=volume_result.get("volume_score"),
)

"scores": score_breakdown,  # Now matches schema!
```

---

### Fix 2: Call technical_analyzer with correct params

```python
# Build price_changes dict from Birdeye + DexScreener
price_changes = {
    "1h": birdeye_data.get("price_change_1h") or dex_data.get("priceChange", {}).get("h1", 0),
    "6h": dex_data.get("priceChange", {}).get("h6", 0),
    "24h": birdeye_data.get("price_change_24h") or dex_data.get("priceChange", {}).get("h24", 0),
}

# Now call with correct params!
momentum_result = tech_analyzer.calculate_momentum_score(price_changes)
volume_result = tech_analyzer.calculate_volume_score(birdeye_data)
trade_pressure_result = tech_analyzer.calculate_trade_pressure_score(birdeye_data)
```

---

### Fix 3: Add missing score calculations

```python
# Whale Risk Score
whale_risk_score = _calculate_whale_risk_score(holder_data)

# Trade Pressure Score
trade_pressure_result = tech_analyzer.calculate_trade_pressure_score(birdeye_data)
```

---

### Fix 4: Build extended_breakdown for UI

```python
extended_breakdown = {
    "technical": {
        "total": 35.5,
        "weight": "55%",
        "components": {
            "momentum": {
                "score": 75,
                "weight": "20%",
                "trend": "BULLISH",
                "details": ["24h: 🟢 BULLISH (+15.5%)"]
            },
            "volume": {
                "score": 82,
                "weight": "15%",
                "buy_pct": 65.2,
                "details": ["🟢 Buy pressure dominant"]
            },
            # ... etc
        }
    },
    "onchain": { ... },
    "social": { ... }
}
```

---

## Files Changed

| File | Changes |
|------|---------|
| `api/routes.py` | Complete rewrite of Step 5-11, fixed score calculation flow |
| `api/schemas.py` | Added default values, fixed ScoreBreakdown structure |

---

## Expected Result After Fix

```
┌─────────────────────────────────────────────────────────────┐
│  TECHNICAL (55%)          ON-CHAIN (30%)      SOCIAL (15%) │
│  35/100                   24/100              9/100        │
│                                                            │
│  Momentum (20%)  75       Holder (12%)  85    Community 60 │
│  Volume (15%)    82       Security (12%) 70   Bot Detect80 │
│  Trade (10%)     70       Whale (6%)    65                 │
│  Liquidity (10%) 90                                        │
└─────────────────────────────────────────────────────────────┘
```

✅ All component scores will now display!
