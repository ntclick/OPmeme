# 📚 Birdeye API Complete Guide

> **Based on:** Official Birdeye Documentation (docs.birdeye.so)  
> **Date:** 2026-02-20  
> **Version:** 3.0 Final

---

## 📊 Package & Rate Limits

### Pricing Tiers

| Tier | Rate Limit | Price | Key Limitations |
|------|------------|-------|-----------------|
| **Standard** | 1 rps | FREE | Chỉ có ~20 endpoints cơ bản |
| **Lite** | 15 rps | $49/mo | Thêm token_security, creation_info |
| **Starter** | 15 rps | $99/mo | Thêm token holder list |
| **Premium** | 50 rps / 1000 rpm | $299/mo | WebSocket, meta-data multiple |
| **Business** | 100 rps / 1500 rpm | $799/mo | Tất cả APIs + batch/multiple |

### Endpoints Available by Package

| Endpoint | Standard | Lite | Starter | Premium | Business |
|----------|----------|------|---------|---------|----------|
| `/defi/price` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/defi/token_overview` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/defi/token_trending` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/defi/v3/token/holder` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/defi/v3/token/market-data` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/defi/v3/token/exit-liquidity` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/defi/v3/search` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/defi/ohlcv` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/holder/v1/distribution` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/defi/multi_price` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `/defi/token_security` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `/defi/token_creation_info` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `/defi/v3/token/trade-data/single` | ❌ | ✅ | ✅ | ✅ | ✅ |
| `/smart-money/v1/token/list` | ❌ | ❌ | ❌ | ✅ | ✅ |
| `/defi/v3/token/market-data/multiple` | ❌ | ❌ | ❌ | ❌ | ✅ |
| `/defi/v3/token/exit-liquidity/multiple` | ❌ | ❌ | ❌ | ❌ | ✅ |

---

## 🔑 Required Headers

```python
headers = {
    "X-API-KEY": "your_birdeye_api_key",
    "x-chain": "solana",  # REQUIRED for all Solana endpoints!
    "accept": "application/json"
}
```

⚠️ **CRITICAL:** `x-chain: solana` header is **REQUIRED** - thiếu header này sẽ trả về 400 Bad Request!

---

## 📋 Endpoint Details

### 1. Token Overview (MOST IMPORTANT)
```
GET https://public-api.birdeye.so/defi/token_overview?address={mint}
```

**Package:** Standard+  
**Rate:** Normal  
**Chain:** All

**Response Fields:**
```json
{
  "success": true,
  "data": {
    // === BASIC INFO ===
    "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "decimals": 5,
    "symbol": "BONK",
    "name": "Bonk",
    "logoURI": "https://...",
    
    // === MARKET DATA ===
    "price": 0.00002134,
    "liquidity": 25000000,           // USD liquidity
    "mc": 1500000000,                // Market cap
    "realMc": 1380000000,            // Real market cap
    "supply": 93000000000000,        // Total supply
    "circulatingSupply": 69000000000000,
    
    // === HOLDER DATA (Unique to Birdeye!) ===
    "holder": 750000,                // Total holders
    "uniqueWallet24h": 25000,        // Unique traders 24h
    "uniqueWallet24hChangePercent": 5.2,
    
    // === TRADE DATA ===
    "trade24h": 150000,              // Total trades 24h
    "trade24hChangePercent": 12.5,
    "buy24h": 85000,                 // Buy count
    "sell24h": 65000,                // Sell count
    "v24hUSD": 50000000,             // Volume 24h USD
    "v24hChangePercent": 8.3,
    "vBuy24hUSD": 28000000,          // Buy volume USD
    "vSell24hUSD": 22000000,         // Sell volume USD
    
    // === PRICE CHANGES ===
    "priceChange24hPercent": 15.5,
    "priceChange1hPercent": 2.1,
    "priceChange30mPercent": 0.8,
    
    // === HISTORY ===
    "history24hPrice": 0.000018,
    "history1hPrice": 0.0000195,
    
    // === SOCIAL/POPULARITY ===
    "watch": 50000,                  // Watchlist count
    "view24h": 100000,               // Views 24h
    "view24hChangePercent": 25.0,
    
    // === TIMESTAMP ===
    "lastTradeUnixTime": 1708444800,
    "lastTradeHumanTime": "2026-02-20T15:00:00"
  }
}
```

---

### 2. Token Security
```
GET https://public-api.birdeye.so/defi/token_security?address={mint}
```

**Package:** Lite+ (not Standard!)  
**Rate:** Normal  
**Chain:** Not SUI

**Response Fields:**
```json
{
  "success": true,
  "data": {
    // === CREATOR INFO ===
    "creatorAddress": "Creator...",
    "creationTx": "tx_hash...",
    "creationTime": 1700000000,
    "creationSlot": 123456789,
    
    // === AUTHORITY (Critical for Security!) ===
    "mintAuthority": null,           // null = renounced ✅
    "freezeAuthority": null,         // null = renounced ✅
    "ownerAddress": null,
    "ownerBalance": 0,
    "ownerPercentage": 0,
    
    // === HOLDER CONCENTRATION ===
    "top10HolderBalance": 45000000000,
    "top10HolderPercent": 45.5,
    "top10UserBalance": 35000000000,
    "top10UserPercent": 35.2,
    
    // === TOKEN FLAGS ===
    "isTrueToken": true,
    "isToken2022": false,
    "totalSupply": 1000000000,
    
    // === PRE-MARKET ===
    "preMarketHolder": 50,
    "lockInfo": null,
    
    // === METAPLEX ===
    "metaplexUpdateAuthority": null,
    "metaplexUpdateAuthorityBalance": 0,
    "metaplexUpdateAuthorityPercent": 0,
    "mutableMetadata": false
  }
}
```

---

### 3. Token Holder List (V3)
```
GET https://public-api.birdeye.so/defi/v3/token/holder?address={mint}&offset=0&limit=100
```

**Package:** Standard+  
**Rate:** Normal  
**Chain:** Solana only

**Parameters:**
| Param | Type | Default | Max |
|-------|------|---------|-----|
| address | string | required | - |
| offset | int | 0 | - |
| limit | int | 100 | 10,000 |

**Response:**
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "address": "5Q544fK...wallet1",
        "amount": "50000000000",
        "decimals": 5,
        "owner": "owner_address",
        "uiAmount": 500000.0,
        "percentage": 25.5
      }
    ],
    "totalItems": 750000
  }
}
```

---

### 4. Token Market Data (V3)
```
GET https://public-api.birdeye.so/defi/v3/token/market-data?address={mint}
```

**Package:** Standard+  
**Response:**
```json
{
  "success": true,
  "data": {
    "address": "DezXAZ8z7...",
    "price": 0.00002134,
    "priceChange24hPercent": 15.5,
    "liquidity": 25000000,
    "mc": 1500000000,
    "realMc": 1380000000,
    "supply": 93000000000000,
    "circulatingSupply": 69000000000000
  }
}
```

---

### 5. Token Exit Liquidity (V3)
```
GET https://public-api.birdeye.so/defi/v3/token/exit-liquidity?address={mint}
```

**Package:** Standard+  
**Response:**
```json
{
  "success": true,
  "data": {
    "address": "DezXAZ8z7...",
    "liquidity": 25000000,
    "liquidityPercentageChange24h": 5.2
  }
}
```

---

### 6. Token Trade Data (V3)
```
GET https://public-api.birdeye.so/defi/v3/token/trade-data/single?address={mint}
```

**Package:** Lite+ (not Standard!)  
**Response:**
```json
{
  "success": true,
  "data": {
    "address": "DezXAZ8z7...",
    "holder": 750000,
    "uniqueWallet24h": 25000,
    "uniqueWallet8h": 12000,
    "trade24h": 150000,
    "trade8h": 75000,
    "buy24h": 85000,
    "sell24h": 65000,
    "buy8h": 42000,
    "sell8h": 33000,
    "v24hUSD": 50000000,
    "v8hUSD": 25000000,
    "vBuy24hUSD": 28000000,
    "vSell24hUSD": 22000000,
    "vBuy8hUSD": 14000000,
    "vSell8hUSD": 11000000,
    "v24hChangePercent": 8.3,
    "v8hChangePercent": 5.1
  }
}
```

---

### 7. Token Creation Info
```
GET https://public-api.birdeye.so/defi/token_creation_info?address={mint}
```

**Package:** Lite+ (not Standard!)  
**Chain:** Solana only

**Response:**
```json
{
  "success": true,
  "data": {
    "txHash": "creation_tx_hash...",
    "slot": 123456789,
    "tokenAddress": "DezXAZ8z7...",
    "decimals": 5,
    "owner": "Creator...",
    "blockUnixTime": 1700000000,
    "blockHumanTime": "2023-11-15T10:00:00Z"
  }
}
```

---

### 8. Token Trending
```
GET https://public-api.birdeye.so/defi/token_trending?sort_by=rank&sort_type=asc&limit=20
```

**Package:** Standard+  
**Parameters:**
| Param | Type | Options | Default |
|-------|------|---------|---------|
| sort_by | string | rank, liquidity, volume24hUSD | rank |
| sort_type | string | asc, desc | asc |
| offset | int | 0-N | 0 |
| limit | int | 1-20 | 20 |

**Response:**
```json
{
  "success": true,
  "data": {
    "updateUnixTime": 1720012620,
    "updateTime": "2024-07-03T13:17:00",
    "tokens": [
      {
        "address": "token_address...",
        "decimals": 6,
        "liquidity": 572411.04,
        "logoURI": "https://...",
        "name": "Token Name",
        "symbol": "SYMBOL",
        "volume24hUSD": 2793145.10,
        "rank": 0
      }
    ],
    "total": 1000
  }
}
```

---

### 9. Price
```
GET https://public-api.birdeye.so/defi/price?address={mint}&include_liquidity=true
```

**Package:** Standard+  
**Response:**
```json
{
  "success": true,
  "data": {
    "value": 0.00002134,
    "updateUnixTime": 1708444800,
    "updateHumanTime": "2026-02-20T15:00:00",
    "liquidity": 25000000
  }
}
```

---

### 10. Multi Price
```
GET https://public-api.birdeye.so/defi/multi_price?list_address={addr1},{addr2},{addr3}
```

**Package:** Lite+ (not Standard!)  
**Response:**
```json
{
  "success": true,
  "data": {
    "addr1": {
      "value": 0.00002134,
      "updateUnixTime": 1708444800,
      "liquidity": 25000000
    },
    "addr2": {
      "value": 150.25,
      "updateUnixTime": 1708444800,
      "liquidity": 100000000
    }
  }
}
```

---

### 11. Search
```
GET https://public-api.birdeye.so/defi/v3/search?keyword={query}&chain=solana&limit=10
```

**Package:** Standard+  
**Response:**
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "address": "token_address...",
        "name": "Token Name",
        "symbol": "SYMBOL",
        "decimals": 6,
        "logoURI": "https://...",
        "liquidity": 25000000,
        "mc": 1500000000,
        "v24hUSD": 50000000
      }
    ]
  }
}
```

---

### 12. Holder Distribution
```
GET https://public-api.birdeye.so/holder/v1/distribution?address={mint}
```

**Package:** Standard+  
**Chain:** Solana only

**Response:**
```json
{
  "success": true,
  "data": {
    "totalHolders": 750000,
    "distribution": [
      {"range": "0-100", "holders": 500000, "percent": 66.67},
      {"range": "100-1000", "holders": 150000, "percent": 20.0},
      {"range": "1000-10000", "holders": 75000, "percent": 10.0},
      {"range": "10000-100000", "holders": 20000, "percent": 2.67},
      {"range": "100000+", "holders": 5000, "percent": 0.67}
    ]
  }
}
```

---

### 13. Smart Money Token List
```
GET https://public-api.birdeye.so/smart-money/v1/token/list?limit=20
```

**Package:** Premium+ (not Standard/Lite/Starter!)  
**Response:**
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "address": "token_address...",
        "symbol": "SYMBOL",
        "name": "Token Name",
        "smartMoneyNetBuy24h": 5000000,
        "smartMoneyBuyVolume24h": 8000000,
        "smartMoneySellVolume24h": 3000000
      }
    ]
  }
}
```

---

## 🎯 Integration với Token Analyzer

### Recommended Strategy

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA SOURCE PRIORITY                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Birdeye token_overview (1 call = most data)                 │
│     ├── Holder count ✅ (DexScreener doesn't have!)             │
│     ├── Unique traders 24h ✅                                   │
│     ├── Buy/Sell counts ✅                                      │
│     ├── Volume breakdown ✅                                     │
│     ├── Price changes ✅                                        │
│     └── Liquidity ✅                                            │
│                                                                 │
│  2. Birdeye token_security (security checks)                    │
│     ├── Mint authority ✅                                       │
│     ├── Freeze authority ✅                                     │
│     ├── Top 10 holder % ✅                                      │
│     └── Creator info ✅                                         │
│                                                                 │
│  3. DexScreener (FREE backup if Birdeye fails)                  │
│     ├── Liquidity ✅                                            │
│     ├── Price changes ✅                                        │
│     ├── Volume ✅                                               │
│     └── Pair age ✅                                             │
│                                                                 │
│  4. Solana RPC (on-chain verification)                          │
│     └── Token info ✅                                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### What Birdeye Has That DexScreener Doesn't:

| Data | Birdeye | DexScreener |
|------|---------|-------------|
| **Holder Count** | ✅ | ❌ |
| **Unique Traders 24h** | ✅ | ❌ |
| **Top Holder List** | ✅ | ❌ |
| **Top 10 Holder %** | ✅ | ❌ |
| **Mint Authority** | ✅ | ❌ |
| **Freeze Authority** | ✅ | ❌ |
| **Creator Address** | ✅ | ❌ |
| **Token Creation Time** | ✅ | ❌ (pair age only) |
| Buy Volume Breakdown | ✅ | ❌ |
| Sell Volume Breakdown | ✅ | ❌ |
| Smart Money Activity | ✅ (Premium+) | ❌ |

---

## ⚡ Quick Test Commands

```bash
# Test token_overview (Standard tier)
curl -H "X-API-KEY: YOUR_KEY" -H "x-chain: solana" \
  "https://public-api.birdeye.so/defi/token_overview?address=DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# Test token_security (Lite+ tier)
curl -H "X-API-KEY: YOUR_KEY" -H "x-chain: solana" \
  "https://public-api.birdeye.so/defi/token_security?address=DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# Test token_holder (Standard tier)
curl -H "X-API-KEY: YOUR_KEY" -H "x-chain: solana" \
  "https://public-api.birdeye.so/defi/v3/token/holder?address=DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263&limit=20"

# Test trending (Standard tier)
curl -H "X-API-KEY: YOUR_KEY" -H "x-chain: solana" \
  "https://public-api.birdeye.so/defi/token_trending?sort_by=rank&limit=10"

# Test search (Standard tier)
curl -H "X-API-KEY: YOUR_KEY" -H "x-chain: solana" \
  "https://public-api.birdeye.so/defi/v3/search?keyword=BONK&limit=5"
```

---

## 🚨 Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| 400 Bad Request | Missing `x-chain` header | Add `x-chain: solana` |
| 403 Forbidden | Invalid API key or endpoint not in plan | Check API key, upgrade plan |
| 429 Too Many Requests | Rate limit exceeded | Add backoff, reduce calls |
| success: false | Invalid token address | Verify mint address |

---

*Generated from Birdeye Official Documentation — 2026-02-20*
