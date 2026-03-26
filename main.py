# main.py (Clean Version)
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
import asyncio
import time
import logging


from api.routes import router
from database.engine import init_db

STATIC_DIR = Path(__file__).resolve().parent / "static"

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

# ── Rate Limiter Middleware ────────────────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory rate limiter: N requests/minute per IP.
    MVP uses dict storage. Scale up → Redis.
    """

    def __init__(self, app, requests_per_minute: int = 10):
        super().__init__(app)
        self.rpm = requests_per_minute
        self.requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Only rate-limit the heavy analysis endpoint
        if "/api/analyze" not in request.url.path:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # Prune entries older than 60 seconds
        self.requests[client_ip] = [
            t for t in self.requests[client_ip] if now - t < 60
        ]

        if len(self.requests[client_ip]) >= self.rpm:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limited",
                    "detail": f"Max {self.rpm} requests/minute. Try again shortly.",
                },
            )

        self.requests[client_ip].append(now)
        return await call_next(request)


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CoinCheckGo API",
    description="Verifiable Meme Coin Scanner for Solana",
    version="1.0.0",
)

# CORS — allow all origins for MVP, restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.add_middleware(RateLimitMiddleware, requests_per_minute=10)

# Routes
app.include_router(router)

# Serve static assets (CSS, JS, images under /static)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def startup():
    """Create DB tables + start PumpScan monitor background tasks."""
    init_db()

    # Start PumpScan background tasks
    from monitor.health_checker import health_checker_loop
    from monitor.lp_detector import lp_detector_loop
    from monitor.pumpfun_ws import pumpfun_ws_loop
    from monitor.telegram_bot import start_telegram_bot

    asyncio.create_task(pumpfun_ws_loop())                    # real-time, <1s latency
    asyncio.create_task(lp_detector_loop(interval_seconds=30))  # fallback poll
    asyncio.create_task(health_checker_loop(interval_seconds=120))
    asyncio.create_task(start_telegram_bot())


@app.on_event("shutdown")
async def shutdown():
    """Close shared HTTP client on shutdown."""
    from api.routes import _shared_http_client
    if _shared_http_client and not _shared_http_client.is_closed:
        await _shared_http_client.aclose()


@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Serve the frontend SPA."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/memerader", include_in_schema=False)
async def memerader_page():
    """Serve the MemeRadar dashboard."""
    return FileResponse(str(STATIC_DIR / "monitor.html"))


@app.get("/monitor", include_in_schema=False)
async def monitor_redirect():
    """Redirect old /monitor URL to /memerader."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/memerader")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve favicon to avoid 404 noise in browser console."""
    return FileResponse(str(STATIC_DIR / "favicon.svg"))


@app.get("/api/health")
async def health():
    return {
        "status": "CoinCheckGo API is running 🚀",
        "version": "1.0.0",
        "docs": "/docs",
    }

