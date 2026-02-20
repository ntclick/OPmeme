# CoinCheckGo — Verifiable Meme Coin Scanner (Solana)

A FastAPI backend that analyzes Solana meme coins by combining **Twitter social data**, **on-chain metrics**, and **OpenGradient verifiable AI** to produce a trust score from 0–100.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill environment variables
cp .env.example .env
# Edit .env with your API keys (Apify, Helius, OpenGradient)

# 3. Run the server
uvicorn main:app --reload --port 8000

# 4. Open API docs
# http://localhost:8000/docs
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/analyze` | Analyze a meme coin (main endpoint) |
| `GET`  | `/api/coin/{ticker}/history` | Past analysis reports for a coin |
| `GET`  | `/api/trending` | Top checked coins in last 24h |
| `GET`  | `/` | Health check |

### Example — Analyze a coin

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "PEPE", "contract_address": "optional_mint_address"}'
```

### Response shape

```json
{
  "ticker": "PEPE",
  "overall_score": 72,
  "risk_level": "LOW",
  "scores": {
    "social_score": 78,
    "bot_score": 85,
    "sentiment_score": 65,
    "holder_score": 60
  },
  "verdict": "Strong community, clean on-chain. DYOR.",
  "red_flags": [],
  "green_flags": ["Strong organic community", "Low bot activity"],
  "tx_hash": "0xabc...",
  "verify_url": "https://explorer.opengradient.ai/tx/0xabc..."
}
```

## Project Structure

```
coincheckgo/
├── main.py                  # FastAPI entry point
├── config.py                # Environment variable loading
├── database/
│   ├── models.py            # SQLAlchemy ORM models
│   ├── engine.py            # DB engine + session
│   └── crud.py              # CRUD operations
├── scrapers/
│   ├── twitter_scraper.py   # Apify Twitter Scraper Lite
│   └── solana_scraper.py    # Solana RPC / Helius
├── analysis/
│   ├── scoring.py           # Social + holder + overall scoring
│   ├── bot_detector.py      # Bot/spam detection
│   ├── sentiment.py         # Rule-based sentiment
│   └── opengradient.py      # OpenGradient verifiable AI
├── api/
│   ├── schemas.py           # Pydantic models
│   └── routes.py            # API endpoints
├── utils/
│   ├── cache.py             # In-memory TTL cache
│   └── helpers.py           # Retry decorator, utilities
├── requirements.txt
└── .env.example
```

## Risk Levels

| Score | Level | Meaning |
|:---:|:---:|:---|
| 0–20 | EXTREME | Almost certainly scam |
| 21–40 | HIGH | Many red flags |
| 41–60 | MEDIUM | Needs more research |
| 61–80 | LOW | Looks decent, DYOR |
| 81–100 | MOON | Strong community + clean on-chain |

## Environment Variables

See `.env.example` for all required keys:
- `APIFY_API_TOKEN` — Twitter scraping via Apify
- `HELIUS_API_KEY` — Solana on-chain data (recommended)
- `OPENGRADIENT_PRIVATE_KEY` — Verifiable AI inference
- `DATABASE_URL` — SQLite (default) or PostgreSQL

## License

MIT
