# analysis/liquidity_analyzer.py — Liquidity Analysis v3.1

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class LiquidityAnalyzer:
    """
    Calculate Liquidity Depth Score (10% of final score in v3.1).
    """
    
    def calculate_liquidity_score(
        self,
        birdeye_data: dict,
        holder_data: dict = None
    ) -> dict:
        """
        Liquidity Depth Score (10% weight)
        
        Components:
        - Liquidity USD (50%)
        - Liquidity/MCap Ratio (30%)
        - Liquidity Change 24h (20%)
        
        Args:
            birdeye_data: Data from Birdeye API
            holder_data: Optional holder data for LP status
            
        Returns:
            dict with liquidity_score, liquidity_usd, details, etc.
        """
        result = {
            "liquidity_score": 50,
            "liquidity_usd": 0,
            "liquidity_mcap_ratio": 0,
            "lp_status": "UNKNOWN",
            "details": []
        }
        
        if not birdeye_data:
            result["details"].append("No market data available")
            return result
        
        # Extract values with safe defaults
        liquidity = birdeye_data.get("liquidity", 0) or 0
        market_cap = birdeye_data.get("market_cap") or 0
        liq_change = birdeye_data.get("liquidity_change_24h", 0) or 0
        
        result["liquidity_usd"] = liquidity
        
        # ─────────────────────────────────────────────────────────────────────
        # 1. Liquidity USD (50% weight)
        # ─────────────────────────────────────────────────────────────────────
        if liquidity >= 5_000_000:
            score_usd = 100
            msg_usd = "🟢 Excellent liquidity - safe for large trades"
        elif liquidity >= 1_000_000:
            score_usd = 90
            msg_usd = "🟢 Very good liquidity"
        elif liquidity >= 500_000:
            score_usd = 80
            msg_usd = "🟢 Good liquidity"
        elif liquidity >= 200_000:
            score_usd = 70
            msg_usd = "🟢 Adequate liquidity"
        elif liquidity >= 100_000:
            score_usd = 55
            msg_usd = "🟡 Moderate - watch slippage"
        elif liquidity >= 50_000:
            score_usd = 40
            msg_usd = "🟡 Low - expect slippage"
        elif liquidity >= 20_000:
            score_usd = 25
            msg_usd = "🔴 Very low - high slippage risk"
        elif liquidity >= 10_000:
            score_usd = 15
            msg_usd = "🔴 Micro liquidity - dangerous"
        else:
            score_usd = 5
            msg_usd = "🔴 CRITICAL - no exit possible"
        
        result["details"].append(f"Depth: {msg_usd} (${liquidity:,.0f})")
        
        # ─────────────────────────────────────────────────────────────────────
        # 2. Liquidity / MCap Ratio (30% weight)
        # ─────────────────────────────────────────────────────────────────────
        if market_cap and market_cap > 0:
            ratio = liquidity / market_cap
            result["liquidity_mcap_ratio"] = round(ratio, 4)

            if ratio >= 0.20:
                score_ratio = 100
                msg_ratio = "🟢 Extremely healthy ratio"
            elif ratio >= 0.10:
                score_ratio = 90
                msg_ratio = "🟢 Very healthy ratio"
            elif ratio >= 0.05:
                score_ratio = 75
                msg_ratio = "🟢 Healthy ratio"
            elif ratio >= 0.03:
                score_ratio = 60
                msg_ratio = "🟡 Acceptable ratio"
            elif ratio >= 0.01:
                score_ratio = 40
                msg_ratio = "🟡 Low ratio - exit may be difficult"
            elif ratio >= 0.005:
                score_ratio = 25
                msg_ratio = "🔴 Poor ratio - high slippage"
            else:
                score_ratio = 10
                msg_ratio = "🔴 RUG RISK - can't exit large positions"

            result["details"].append(f"Ratio: {msg_ratio} ({ratio*100:.2f}% of MCap)")
        else:
            result["liquidity_mcap_ratio"] = None
            score_ratio = 45
            result["details"].append("Ratio: 🟡 Market cap unavailable - cannot compute Liquidity/MCap ratio")
        
        # ─────────────────────────────────────────────────────────────────────
        # 3. Liquidity Change 24h (20% weight)
        # ─────────────────────────────────────────────────────────────────────
        if liq_change > 50:
            score_change = 90
            msg_change = "🟢 Liquidity growing fast"
        elif liq_change >= 20:
            score_change = 80
            msg_change = "🟢 Liquidity increasing"
        elif liq_change >= 0:
            score_change = 60
            msg_change = "🟡 Liquidity stable"
        elif liq_change >= -10:
            score_change = 45
            msg_change = "🟡 Slight liquidity decrease"
        elif liq_change >= -30:
            score_change = 30
            msg_change = "🔴 Liquidity decreasing"
        else:
            score_change = 10
            msg_change = "🔴 Liquidity draining fast!"
        
        if liq_change != 0:
            result["details"].append(f"Change: {msg_change} ({liq_change:+.1f}%)")
        
        # ─────────────────────────────────────────────────────────────────────
        # Final Score Calculation
        # ─────────────────────────────────────────────────────────────────────
        final_score = int(
            (score_usd * 0.50) + 
            (score_ratio * 0.30) + 
            (score_change * 0.20)
        )
        result["liquidity_score"] = max(0, min(100, final_score))
        
        # ─────────────────────────────────────────────────────────────────────
        # LP Status (informational only)
        # ─────────────────────────────────────────────────────────────────────
        if holder_data:
            lp_burned = holder_data.get("lp_burned_pct")
            lp_locked = holder_data.get("liquidity_locked")
            if lp_burned and lp_burned > 90:
                result["lp_status"] = "BURNED"
                result["details"].append("🟢 LP tokens burned")
            elif lp_locked:
                result["lp_status"] = "LOCKED"
                result["details"].append("🟢 LP locked")
        
        return result
