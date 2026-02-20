# config.py — Environment variables & constants

import os
from pathlib import Path
from dotenv import load_dotenv

# Walk up from this file to find the first .env (supports running from coincheckgo/)
_here = Path(__file__).resolve().parent
for _dir in [_here, _here.parent, _here.parent.parent]:
    _env_path = _dir / ".env"
    if _env_path.is_file():
        load_dotenv(_env_path)
        break
else:
    load_dotenv()  # fallback to default search

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./coincheckgo.db")

# Apify (Twitter scraping)
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")

# OpenGradient (Verifiable AI)
# Prefer OPENGRADIENT_PRIVATE_KEY, fallback to OG_PRIVATE_KEY used in official examples
OPENGRADIENT_PRIVATE_KEY = os.getenv("OPENGRADIENT_PRIVATE_KEY") or os.getenv("OG_PRIVATE_KEY")

# MemSync (Long-term memory)
MEMSYNC_API_KEY = os.getenv("MEMSYNC_API_KEY")

# Solana RPC
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

# App config
CACHE_TTL_MINUTES = int(os.getenv("CACHE_TTL_MINUTES", "15"))
MAX_TWEETS_PER_SCAN = int(os.getenv("MAX_TWEETS_PER_SCAN", "100"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
