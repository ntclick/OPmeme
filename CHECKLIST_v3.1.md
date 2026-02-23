# 📋 AI AGENT TASK CHECKLIST — v3.1 Technical Focus

## 🎯 MISSION
Refactor Solana Token Analyzer to disable Twitter/Social and focus on Technical analysis.

---

## 📁 FILES TO REPLACE

| File | Action | Notes |
|------|--------|-------|
| `api/routes.py` | **REPLACE** | Main file - all changes here |
| `analysis/liquidity_analyzer.py` | **REPLACE** | Fixed function signature |
| `scrapers/birdeye_client.py` | **REPLACE** | Better error handling |
| `api/schemas.py` | Keep | No changes needed |
| `analysis/technical_analyzer.py` | Keep | No changes needed |

---

## 🔄 WEIGHT CHANGES

**OLD (v3.0):**
```
Technical: 55%
On-Chain:  30%
Social:    15%
```

**NEW (v3.1):**
```
Technical: 65% (+10%)
On-Chain:  35% (+5%)
Social:    0%  (DISABLED)
```

---

## 📊 NEW COMPONENT WEIGHTS

### Technical (65% total)
| Component | Old | New |
|-----------|-----|-----|
| Momentum | 20% | **25%** |
| Volume | 15% | **18%** |
| Trade Pressure | 10% | **12%** |
| Liquidity | 10% | 10% |

### On-Chain (35% total)
| Component | Old | New |
|-----------|-----|-----|
| Holder | 12% | **15%** |
| Security | 12% | 12% |
| Whale Risk | 6% | **8%** |

### Social (0% - DISABLED)
| Component | Old | New |
|-----------|-----|-----|
| Community | 10% | 0% |
| Bot Detection | 5% | 0% |

---

## ✅ KEY FIXES INCLUDED

1. **Override reasons → RED FLAGS** (was missing!)
   ```python
   if overall.get("overrides"):
       red_flags.extend(overall["overrides"])
   ```

2. **liquidity_analyzer signature fixed**
   ```python
   # OLD (wrong):
   def calculate_liquidity_score(self, contract_address, birdeye_data, holder_data):
   
   # NEW (correct):
   def calculate_liquidity_score(self, birdeye_data: dict, holder_data: dict = None):
   ```

3. **Twitter scraping removed**
   - No more `twitter_scraper` imports
   - No more `calculate_bot_score()` calls
   - `tweets_analyzed` always = 0

4. **Better Birdeye error handling**
   - Proper 400/403/429 handling
   - Logging for debugging

---

## 🧪 TESTING

After replacing files:

```bash
# Clear cache
curl -X POST http://localhost:8000/api/admin/clear-cache

# Test analysis
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "BONK", "force_refresh": true}'
```

**Verify:**
- [ ] All component scores show values (not --)
- [ ] RED FLAGS show override reasons
- [ ] Social section shows "DISABLED"
- [ ] Weight totals are 65% Technical + 35% On-Chain

---

## 📝 EXPECTED RESPONSE STRUCTURE

```json
{
  "overall_score": 72,
  "risk_level": "LOW",
  "breakdown": {
    "technical": {
      "total": 46.8,
      "weight": "65%"
    },
    "onchain": {
      "total": 25.2,
      "weight": "35%"
    },
    "social": {
      "total": 0,
      "weight": "0%",
      "status": "DISABLED"
    }
  },
  "red_flags": [
    "🟡 Override reason if any"
  ],
  "green_flags": [
    "🟢 Positive signals"
  ],
  "algorithm_notes": [
    "Scoring: v3.1 (Technical 65% / On-Chain 35%)",
    "Social: DISABLED"
  ]
}
```

---

## 🚀 DEPLOYMENT STEPS

```powershell
# 1. Backup existing files
Copy-Item "api\routes.py" "api\routes.py.v30.bak"
Copy-Item "analysis\liquidity_analyzer.py" "analysis\liquidity_analyzer.py.bak"
Copy-Item "scrapers\birdeye_client.py" "scrapers\birdeye_client.py.bak"

# 2. Replace with new files
# (copy from outputs folder)

# 3. Clear Python cache
Remove-Item -Recurse -Force __pycache__ -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force api\__pycache__ -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force analysis\__pycache__ -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force scrapers\__pycache__ -ErrorAction SilentlyContinue

# 4. Restart server
uvicorn main:app --reload
```

---

*v3.1 Task Checklist — 2026-02-22*
