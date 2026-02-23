# scrapers/jupiter_client.py — Jupiter Aggregator API Client

import httpx
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# SOL mint address
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class JupiterClient:
    """Jupiter Aggregator API for swap quotes and slippage analysis."""
    
    BASE_URL = "https://quote-api.jup.ag/v6"
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50
    ) -> Optional[dict]:
        """
        Get swap quote from Jupiter.
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount: Amount in smallest unit (lamports for SOL)
            slippage_bps: Slippage tolerance in basis points (50 = 0.5%)
        """
        async with httpx.AsyncClient() as client:
            try:
                url = f"{self.BASE_URL}/quote"
                params = {
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount),
                    "slippageBps": slippage_bps,
                }
                resp = await client.get(url, params=params, timeout=10)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.warning(f"Jupiter quote failed: {e}")
                return None
    
    async def check_slippage(
        self,
        token_mint: str,
        test_amounts_sol: list[float] = None
    ) -> dict:
        """
        Test slippage at different trade sizes.
        
        Returns:
        {
            "slippage_0.1_sol": 0.05,   # 0.05% price impact
            "slippage_1_sol": 0.15,
            "slippage_10_sol": 1.2,
            "avg_slippage": 0.47,
            "liquidity_grade": "A"       # A/B/C/D/F
        }
        """
        if test_amounts_sol is None:
            test_amounts_sol = [0.1, 1, 10]
            
        results = {}
        slippages = []
        
        for sol_amount in test_amounts_sol:
            lamports = int(sol_amount * 1_000_000_000)  # Convert to lamports
            
            quote = await self.get_quote(
                input_mint=SOL_MINT,
                output_mint=token_mint,
                amount=lamports
            )
            
            if quote:
                impact = float(quote.get("priceImpactPct", 0))
                results[f"slippage_{sol_amount}_sol"] = round(impact, 4)
                slippages.append(impact)
        
        # Calculate average and grade
        if slippages:
            avg = sum(slippages) / len(slippages)
            results["avg_slippage"] = round(avg, 4)
            results["liquidity_grade"] = self._grade_slippage(avg, slippages[-1])
        else:
            results["avg_slippage"] = None
            results["liquidity_grade"] = "UNKNOWN"  # Network error, not bad liquidity
            results["error"] = "Could not fetch quotes"
        
        return results
    
    def _grade_slippage(self, avg: float, max_slippage: float) -> str:
        """Grade liquidity based on slippage."""
        # Based on 10 SOL trade slippage
        if max_slippage < 0.5:
            return "A"   # Excellent liquidity
        elif max_slippage < 1.0:
            return "B"   # Good
        elif max_slippage < 2.0:
            return "C"   # Moderate
        elif max_slippage < 5.0:
            return "D"   # Poor
        else:
            return "F"   # Very poor / honeypot risk
    
    async def check_honeypot(self, token_mint: str) -> dict:
        """
        Basic honeypot detection via buy/sell quote comparison.
        
        Logic:
        1. Get quote: SOL → Token (buy)
        2. Get quote: Token → SOL (sell)
        3. Compare: If sell gives significantly less, might be honeypot
        """
        test_sol = 0.1  # 0.1 SOL test
        lamports = int(test_sol * 1_000_000_000)
        
        # Buy quote
        buy_quote = await self.get_quote(
            input_mint=SOL_MINT,
            output_mint=token_mint,
            amount=lamports
        )
        
        if not buy_quote:
            return {"honeypot_risk": "UNKNOWN", "reason": "Cannot get buy quote"}
        
        token_amount = int(buy_quote.get("outAmount", 0))
        if token_amount == 0:
            return {"honeypot_risk": "HIGH", "reason": "Zero output on buy"}
        
        # Sell quote (reverse)
        sell_quote = await self.get_quote(
            input_mint=token_mint,
            output_mint=SOL_MINT,
            amount=token_amount
        )
        
        if not sell_quote:
            return {"honeypot_risk": "HIGH", "reason": "Cannot sell - possible honeypot"}
        
        sol_back = int(sell_quote.get("outAmount", 0))
        sol_back_amount = sol_back / 1_000_000_000
        
        # Calculate round-trip loss
        loss_pct = ((test_sol - sol_back_amount) / test_sol) * 100
        
        if loss_pct > 50:
            risk = "HIGH"
            reason = f"Round-trip loss: {loss_pct:.1f}% (>50%)"
        elif loss_pct > 20:
            risk = "MEDIUM"
            reason = f"Round-trip loss: {loss_pct:.1f}% (high fees or tax)"
        elif loss_pct > 5:
            risk = "LOW"
            reason = f"Round-trip loss: {loss_pct:.1f}% (normal)"
        else:
            risk = "SAFE"
            reason = f"Round-trip loss: {loss_pct:.1f}%"
        
        return {
            "honeypot_risk": risk,
            "round_trip_loss_pct": round(loss_pct, 2),
            "reason": reason,
            "buy_impact": buy_quote.get("priceImpactPct"),
            "sell_impact": sell_quote.get("priceImpactPct"),
        }
