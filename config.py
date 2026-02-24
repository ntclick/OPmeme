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
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Default to /tmp for Railway compatibility (ephemeral filesystem)
    import tempfile
    fallback_path = Path(tempfile.gettempdir()) / "coincheckgo.db"
    DATABASE_URL = f"sqlite:///{fallback_path}"

# Ensure SQLite directory exists and is writable
if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if db_path and db_path != ":memory:":
        # Convert relative to absolute
        if db_path.startswith("./"):
            db_path = str(_here / db_path[2:])
        
        db_dir = os.path.dirname(db_path)
        
        try:
            # Try to create directory if it doesn't exist
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            # Try to open/create the file to test write permissions
            with open(db_path, "a") as f:
                pass
            DATABASE_URL = f"sqlite:///{db_path}"
        except Exception:
            # Fallback to /tmp if current path is read-only (like Railway without volumes)
            import tempfile
            fallback_path = Path(tempfile.gettempdir()) / "coincheckgo.db"
            DATABASE_URL = f"sqlite:///{fallback_path}"
            print(f"⚠️ Using fallback database: {DATABASE_URL}")

# Apify (Twitter scraping)
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
APIFY_TWITTER_ACTOR_ID = os.getenv("APIFY_TWITTER_ACTOR_ID", "nfp1fpt5gUlBwPcor")

# OpenGradient (Verifiable AI)
# Prefer OPENGRADIENT_PRIVATE_KEY, fallback to OG_PRIVATE_KEY used in official examples
OPENGRADIENT_PRIVATE_KEY = os.getenv("OPENGRADIENT_PRIVATE_KEY") or os.getenv("OG_PRIVATE_KEY")
OPENGRADIENT_EMAIL = os.getenv("OPENGRADIENT_EMAIL", "")
OPENGRADIENT_PASSWORD = os.getenv("OPENGRADIENT_PASSWORD", "")
OPENGRADIENT_FIREBASE_API_KEY = os.getenv("OPENGRADIENT_FIREBASE_API_KEY", "")

# MemSync (Long-term memory)
MEMSYNC_API_KEY = os.getenv("MEMSYNC_API_KEY")

# Solana RPC
SOLANA_RPC_URL = os.getenv("ALCHEMY_RPC_URL", os.getenv("HELIUS_RPC_URL", "https://api.mainnet-beta.solana.com"))
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
ALCHEMY_RPC_URL = os.getenv("ALCHEMY_RPC_URL")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")

# App config
CACHE_TTL_MINUTES = int(os.getenv("CACHE_TTL_MINUTES", "15"))
MAX_TWEETS_PER_SCAN = int(os.getenv("MAX_TWEETS_PER_SCAN", "100"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Network / DNS fallback (for Windows DNS issues)
HTTP_PROXY = os.getenv("HTTP_PROXY", None)
HTTPS_PROXY = os.getenv("HTTPS_PROXY", None)
DNS_FALLBACK_IPS = {
    "public-api.birdeye.so": ["104.18.28.72", "104.18.29.72"],
    "quote-api.jup.ag": ["172.67.134.36", "104.21.32.135"],
    "api.dexscreener.com": ["104.21.42.12", "172.67.143.12"],
}

# Scoring thresholds (tunable)
SCORING_CONFIG = {
    "min_liquidity_usd": 10000,
    "min_holders": 100,
    "max_top10_concentration": 80,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "slippage_test_amounts": [0.1, 1, 10],  # SOL
}
