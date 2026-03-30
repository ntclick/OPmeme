#!/bin/bash
set -e

# --- DNS Resilience Fixes for Railway ---
# Explicitly map TEE gateway and dependency hostnames to bypass DNS resolution issues
echo "13.59.207.188 llm.opengradient.ai" >> /etc/hosts
echo "3.14.94.135 ogevmdevnet.opengradient.ai" >> /etc/hosts
echo "3.14.94.135 eth-devnet.opengradient.ai" >> /etc/hosts
echo "104.18.44.153 sepolia.base.org" >> /etc/hosts

echo "✅ DNS entries injected into /etc/hosts"

# Pre-validation (Optional: helpful for debugging container logs)
ping -c 1 llm.opengradient.ai || echo "⚠️ Ping failed (expected if ICMP blocked, but check logs if connection fails)"

# --- Start Application ---
# We use the PORT environment variable provided by Railway (defaulting to 8080)
echo "🚀 Starting CoinCheckGo on port ${PORT:-8080}..."
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --loop uvloop
