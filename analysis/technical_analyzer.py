# analysis/technical_analyzer.py — Technical Indicators v3.0 (Birdeye-based)

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TechnicalAnalyzer:
    """
    Calculate Technical Analysis indicators (55% of final score).
    
    Data sources: Birdeye API & DexScreener.
    """
    
    def _score_price_change_24h(self, change: float) -> tuple[int, str]:
        if change > 100: return 40, "🔴 OVERBOUGHT (risky to enter)"
        if change > 50: return 55, "🟡 Very bullish but extended"
        if change > 20: return 85, "🟢 STRONG BULLISH"
        if change > 5: return 90, "🟢 BULLISH"
        if change > 0: return 70, "🟢 Slightly bullish"
        if change > -5: return 60, "🟡 Neutral"
        if change > -10: return 50, "🟡 Slightly bearish"
        if change > -20: return 40, "🔴 BEARISH"
        if change > -50: return 30, "🔴 Strong bearish"
        return 20, "🔴 CRASH (potential bounce?)"

    def _score_price_change_1h_6h(self, change: float) -> tuple[int, str]:
        if change > 20: return 50, "🟡 Pump in progress (risky)"
        if change > 10: return 75, "🟢 Strong short-term momentum"
        if change > 5: return 85, "🟢 Good momentum"
        if change > 0: return 70, "🟢 Stable upward"
        if change > -5: return 60, "🟡 Consolidating"
        if change > -10: return 45, "🟡 Short-term weakness"
        return 30, "🔴 Dumping"

    def calculate_momentum_score(self, price_changes: dict) -> dict:
        """
        1. Momentum Score (20% weight)
        """
        result = {
            "momentum_score": 50,
            "rsi_signal": "UNKNOWN",
            "divergence": None,
            "trend": "NEUTRAL",
            "details": []
        }
        
        pc_1h = price_changes.get("1h") or 0
        pc_6h = price_changes.get("6h") or 0
        pc_24h = price_changes.get("24h") or 0

        score_1h, msg_1h = self._score_price_change_1h_6h(pc_1h)
        score_6h, msg_6h = self._score_price_change_1h_6h(pc_6h)
        score_24h, msg_24h = self._score_price_change_24h(pc_24h)
        
        result["details"].append(f"24h: {msg_24h} ({pc_24h:+.1f}%)")
        result["details"].append(f"1h: {msg_1h} ({pc_1h:+.1f}%)")
        
        base_score = (score_1h * 0.25) + (score_6h * 0.25) + (score_24h * 0.30)
        
        # Trend consistency
        trend_bonus = 0
        if pc_1h > 0 and pc_6h > 0 and pc_24h > 0:
            trend_bonus = 15
            result["trend"] = "STRONG_BULLISH"
        elif pc_24h > 0 and pc_1h < 0:
            trend_bonus = 10
            result["trend"] = "PULLBACK"
        elif pc_24h < 0 and pc_1h > 0:
            trend_bonus = 5
            result["trend"] = "POTENTIAL_REVERSAL"
        elif pc_1h < 0 and pc_6h < 0 and pc_24h < 0:
            trend_bonus = -10
            result["trend"] = "STRONG_BEARISH"
        elif pc_24h > 50 and pc_1h < -10:
            trend_bonus = -20
            result["trend"] = "DUMP_AFTER_PUMP"
            
        final_score = max(0, min(100, int(base_score + (trend_bonus * 0.20))))
        result["momentum_score"] = final_score
        
        # Divergence
        if pc_1h < -5 and pc_24h > 10:
            result["divergence"] = "BULLISH"
        elif pc_1h > 5 and pc_24h < -10:
            result["divergence"] = "BEARISH"
            
        return result

    def calculate_volume_score(self, birdeye_data: dict) -> dict:
        """
        2. Volume Score (15% weight)
        """
        result = {
            "volume_score": 50,
            "volume_24h": 0,
            "volume_mcap_ratio": 0,
            "buy_volume_pct": 50,
            "details": []
        }
        
        if not birdeye_data:
            return result
            
        volume_24h = birdeye_data.get("volume_24h", 0) or 0
        buy_volume = birdeye_data.get("buy_volume_24h", 0) or 0
        market_cap = birdeye_data.get("market_cap", 1) or 1
        vol_change = birdeye_data.get("volume_24h_change", 0) or 0
        
        result["volume_24h"] = volume_24h
        
        # Volume/MCap Ratio
        vol_mcap_ratio = volume_24h / market_cap if market_cap > 0 else 0
        result["volume_mcap_ratio"] = round(vol_mcap_ratio, 4)
        
        if vol_mcap_ratio > 1.0: score_ratio, msg_ratio = 95, "🔥 EXTREME activity (may be wash trading)"
        elif vol_mcap_ratio >= 0.5: score_ratio, msg_ratio = 90, "🟢 Very high activity"
        elif vol_mcap_ratio >= 0.2: score_ratio, msg_ratio = 85, "🟢 High activity"
        elif vol_mcap_ratio >= 0.1: score_ratio, msg_ratio = 75, "🟢 Good activity"
        elif vol_mcap_ratio >= 0.05: score_ratio, msg_ratio = 60, "🟡 Moderate activity"
        elif vol_mcap_ratio >= 0.02: score_ratio, msg_ratio = 45, "🟡 Low activity"
        elif vol_mcap_ratio >= 0.01: score_ratio, msg_ratio = 30, "🔴 Very low activity"
        else: score_ratio, msg_ratio = 15, "🔴 DEAD (no trading)"
        
        result["details"].append(msg_ratio)
        
        # Buy Volume Dominance
        buy_pct = (buy_volume / volume_24h * 100) if volume_24h > 0 else 50
        result["buy_volume_pct"] = round(buy_pct, 1)
        
        if buy_pct > 70: score_buy, msg_buy = 95, "🟢 STRONG accumulation"
        elif buy_pct >= 60: score_buy, msg_buy = 85, "🟢 Buy pressure"
        elif buy_pct >= 55: score_buy, msg_buy = 70, "🟢 Slightly bullish"
        elif buy_pct >= 45: score_buy, msg_buy = 50, "🟡 Balanced"
        elif buy_pct >= 40: score_buy, msg_buy = 35, "🟡 Slightly bearish"
        elif buy_pct >= 30: score_buy, msg_buy = 20, "🔴 Sell pressure"
        else: score_buy, msg_buy = 10, "🔴 HEAVY distribution"
        
        result["details"].append(f"Volume Profile: {msg_buy} ({buy_pct:.0f}% Buys)")
        
        # Volume Change
        if vol_change > 100: score_change = 90
        elif vol_change >= 50: score_change = 80
        elif vol_change >= 20: score_change = 70
        elif vol_change >= 0: score_change = 60
        elif vol_change >= -20: score_change = 45
        elif vol_change >= -50: score_change = 30
        else: score_change = 15
        
        final_score = int((score_ratio * 0.40) + (score_buy * 0.30) + (score_change * 0.30))
        result["volume_score"] = max(0, min(100, final_score))
        return result

    def calculate_trade_pressure_score(self, birdeye_data: dict) -> dict:
        """
        3. Trade Pressure Score (10% weight)
        """
        result = {
            "trade_pressure_score": 50,
            "buy_sell_ratio": 1.0,
            "unique_traders": 0,
            "details": []
        }
        
        if not birdeye_data:
            return result
            
        buys = birdeye_data.get("buys_24h", 0) or 0
        sells = birdeye_data.get("sells_24h", 1) or 1
        unique_traders = birdeye_data.get("unique_traders_24h", 0) or 0
        total_trades = birdeye_data.get("trades_24h", 0) or 0
        
        result["unique_traders"] = unique_traders
        
        # Buy/Sell Ratio
        bs_ratio = buys / sells if sells > 0 else 1.0
        result["buy_sell_ratio"] = round(bs_ratio, 2)
        
        if bs_ratio > 3.0: score_bs, msg_bs = 95, "🟢 EXTREME buy pressure"
        elif bs_ratio >= 2.0: score_bs, msg_bs = 90, "🟢 Very strong buy pressure"
        elif bs_ratio >= 1.5: score_bs, msg_bs = 80, "🟢 Strong buy pressure"
        elif bs_ratio >= 1.2: score_bs, msg_bs = 70, "🟢 Moderate buy pressure"
        elif bs_ratio >= 1.0: score_bs, msg_bs = 55, "🟡 Slightly bullish"
        elif bs_ratio >= 0.8: score_bs, msg_bs = 45, "🟡 Slightly bearish"
        elif bs_ratio >= 0.6: score_bs, msg_bs = 30, "🔴 Sell pressure"
        elif bs_ratio >= 0.4: score_bs, msg_bs = 20, "🔴 Strong sell pressure"
        else: score_bs, msg_bs = 10, "🔴 DUMP MODE"
        
        result["details"].append(f"Trades: {msg_bs} (B/S: {bs_ratio:.1f})")
        
        # Unique Traders
        if unique_traders > 10000: score_traders = 100
        elif unique_traders >= 5000: score_traders = 90
        elif unique_traders >= 2000: score_traders = 80
        elif unique_traders >= 1000: score_traders = 70
        elif unique_traders >= 500: score_traders = 55
        elif unique_traders >= 200: score_traders = 40
        elif unique_traders >= 100: score_traders = 25
        else: score_traders = 10
        
        # Trade frequency (trades per user)
        tpu = total_trades / unique_traders if unique_traders > 0 else 0
        if tpu < 2: score_freq = 50
        elif tpu < 5: score_freq = 70
        else: score_freq = 40 # Too high = wash trading / bots
        
        final_score = int((score_bs * 0.50) + (score_traders * 0.30) + (score_freq * 0.20))
        result["trade_pressure_score"] = max(0, min(100, final_score))
        return result
