#!/bin/bash
set -e

# --- DNS Resilience Fixes for Railway ---
# Explicitly map TEE gateway and dependency hostnames to bypass DNS resolution issues
echo "13.59.207.188 llm.opengradient.ai" >> /etc/hosts
echo "3.14.94.135 ogevmdevnet.opengradient.ai" >> /etc/hosts
echo "3.14.94.135 eth-devnet.opengradient.ai" >> /etc/hosts
echo "104.18.44.153 sepolia.base.org" >> /etc/hosts

echo "✅ DNS entries injected into /etc/hosts"

# Pre-validation (python:3.11-slim has no ping — use python instead)
python -c "import urllib.request; urllib.request.urlopen('https://llm.opengradient.ai', timeout=5); print('✅ OpenGradient TEE reachable')" 2>/dev/null || echo "⚠️ OpenGradient TEE unreachable (will retry on first request)"

# --- Start Application ---
# We use the PORT environment variable provided by Railway (defaulting to 8080)
echo "🚀 Starting CoinCheckGo on port ${PORT:-8080}..."
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --loop uvloop
