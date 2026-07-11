"""
Layer 4: Order Flow Analyzer.
Checks volume expansion, open interest, funding rate, and news context.
"""

from typing import Dict, Optional

from core.market_data import MarketData
from utils.logger import get_logger
from config.settings import STRATEGY

logger = get_logger(__name__)


class OrderFlowAnalyzer:
    """
    Layer 4 of the 8-layer architecture.
    Validates entries with volume, OI, funding, and news context.
    """
    
    def __init__(self, market_data: Optional[MarketData] = None):
        self.market_data = market_data or MarketData()
    
    def analyze(self, symbol: str, direction: str) -> Dict:
        """
        Analyze order flow conditions before entry.
        
        Args:
            symbol: Trading pair
            direction: "up" or "down" (expected direction)
        
        Returns:
            Dict with volume, OI, funding checks and overall pass/fail.
        """
        # Volume expansion check
        volume_check = self._check_volume_expansion(symbol)
        
        # Open interest check
        oi_check = self._check_open_interest(symbol, direction)
        
        # Funding rate check
        funding_check = self._check_funding_rate(symbol, direction)
        
        # News/event check (placeholder for scheduled events)
        news_check = self._check_news_context(symbol)
        
        # Determine overall pass
        # Volume is the primary gate
        volume_pass = volume_check["pass"]
        
        # OI and Funding are context, not mandatory gates
        # (unless you change the config)
        oi_pass = True  # Context only
        funding_pass = True  # Context only
        
        if not STRATEGY.funding_context_only:
            # If configured as mandatory gate
            funding_pass = funding_check["pass"]
        
        overall_pass = volume_pass and oi_pass and funding_pass and news_check["pass"]
        
        result = {
            "pass": overall_pass,
            "volume": volume_check,
            "open_interest": oi_check,
            "funding": funding_check,
            "news": news_check,
        }
        
        logger.info(
            f"OrderFlow {symbol}: pass={overall_pass} | "
            f"Vol={volume_check['ratio']:.2f}x | "
            f"OI={oi_check['change_pct']:+.2f}% | "
            f"Fund={funding_check['rate']*100:+.4f}% | "
            f"News={news_check['status']}"
        )
        
        return result
    
    def _check_volume_expansion(self, symbol: str) -> Dict:
        """
        Check if current volume is expanded vs recent average.
        Returns pass if current > 1.5x average (configurable).
        """
        candles = self.market_data.get_candles(
            symbol, "4H", limit=STRATEGY.volume_lookback + 5
        )
        
        if len(candles) < STRATEGY.volume_lookback:
            return {"pass": False, "ratio": 0.0, "current": 0.0, "average": 0.0}
        
        volumes = [c["volume"] for c in candles]
        current_vol = volumes[-1]
        avg_vol = sum(volumes[-STRATEGY.volume_lookback-1:-1]) / STRATEGY.volume_lookback
        
        ratio = current_vol / avg_vol if avg_vol > 0 else 0
        passes = ratio >= STRATEGY.volume_expansion_ratio
        
        return {
            "pass": passes,
            "ratio": round(ratio, 2),
            "current": round(current_vol, 2),
            "average": round(avg_vol, 2),
            "threshold": STRATEGY.volume_expansion_ratio,
        }
    
    def _check_open_interest(self, symbol: str, direction: str) -> Dict:
        """
        Check if open interest is increasing.
        Rising OI + rising price = new money entering longs (bullish).
        Rising OI + falling price = new money entering shorts (bearish).
        """
        try:
            current_oi = self.market_data.get_open_interest(symbol)
            
            # We need historical OI to calculate change
            # For now, store and compare on next call
            # In production, you'd maintain a rolling window
            
            # Placeholder: assume increasing if we can fetch it
            # In real implementation, compare to previous reading
            change_pct = 0.0  # Would be calculated from history
            
            # OI increasing is generally supportive of the move
            oi_increasing = change_pct > 0
            
            return {
                "pass": True,  # Context only, not a hard gate
                "current_oi": current_oi,
                "change_pct": round(change_pct, 4),
                "increasing": oi_increasing,
            }
            
        except Exception as e:
            logger.warning(f"OI check failed for {symbol}: {e}")
            return {
                "pass": True,  # Don't block on missing data
                "current_oi": 0,
                "change_pct": 0.0,
                "increasing": False,
            }
    
    def _check_funding_rate(self, symbol: str, direction: str) -> Dict:
        """
        Check funding rate context.
        Extreme funding = crowded trade, potential reversal risk.
        Used as context/deprioritization, not a hard veto.
        """
        try:
            funding_rate = self.market_data.get_funding_rate(symbol)
            
            # Determine if funding is extreme
            is_extreme = abs(funding_rate) > STRATEGY.funding_extreme_threshold
            
            # Context interpretation
            if funding_rate > 0.001:  # Longs pay shorts
                context = "longs_paying"
                if direction == "up":
                    context += "_unfavorable"  # Crowded long
            elif funding_rate < -0.001:  # Shorts pay longs
                context = "shorts_paying"
                if direction == "down":
                    context += "_unfavorable"  # Crowded short
            else:
                context = "neutral"
            
            # Only fail if configured as mandatory AND extreme
            passes = True
            if not STRATEGY.funding_context_only and is_extreme:
                passes = False
            
            return {
                "pass": passes,
                "rate": funding_rate,
                "is_extreme": is_extreme,
                "context": context,
            }
            
        except Exception as e:
            logger.warning(f"Funding check failed for {symbol}: {e}")
            return {
                "pass": True,
                "rate": 0.0,
                "is_extreme": False,
                "context": "unknown",
            }
    
    def _check_news_context(self, symbol: str) -> Dict:
        """
        Check for scheduled news events and unscheduled volatility.
        
        Scheduled: Hard no-trade window around CPI, FOMC, etc.
        Unscheduled: Kill-switch triggered by abnormal volatility.
        """
        # TODO: Integrate with economic calendar API
        # For now, placeholder that always passes
        
        # In production:
        # 1. Check economic calendar for events in next X minutes
        # 2. If event within buffer window, return pass=False
        # 3. Check recent ATR vs historical - if >3x normal, kill switch
        
        return {
            "pass": True,
            "status": "clear",
            "next_event": None,
            "minutes_to_event": None,
        }
    
    def is_crowded_trade(self, symbol: str, direction: str) -> bool:
        """
        Check if this direction is crowded (extreme funding).
        Used for universe pre-filtering, not per-trade veto.
        """
        funding = self._check_funding_rate(symbol, direction)
        return funding["is_extreme"] and "unfavorable" in funding.get("context", "")