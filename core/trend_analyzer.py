"""
Layer 2: Trend Analyzer.
Evaluates EMA alignment across Daily and 4H timeframes.
"""

from typing import Dict, List, Optional

from core.market_data import MarketData
from utils.helpers import calculate_ema, is_trending_up, is_trending_down
from utils.logger import get_logger
from config.settings import STRATEGY

logger = get_logger(__name__)


class TrendAnalyzer:
    """
    Layer 2 of the 8-layer architecture.
    Checks Daily and 4H EMA alignment for trend direction.
    """
    
    def __init__(self, market_data: Optional[MarketData] = None):
        self.market_data = market_data or MarketData()
    
    def analyze(self, symbol: str) -> Dict:
        """
        Analyze trend across multiple timeframes.
        
        Returns:
            Dict with:
                - direction: "up", "down", "neutral"
                - daily_aligned: bool
                - fourh_aligned: bool
                - score: float (0-1, weighted alignment)
                - details: dict with EMA values
        """
        # Fetch both timeframes
        daily_candles = self.market_data.get_candles(
            symbol, "1D", limit=STRATEGY.ema_slow_daily + 10
        )
        fourh_candles = self.market_data.get_candles(
            symbol, "4H", limit=STRATEGY.ema_slow_4h + 10
        )
        
        # Need minimum 4H data
        if len(fourh_candles) < STRATEGY.ema_slow_4h:
            logger.warning(f"{symbol}: Insufficient 4H data")
            return self._neutral_result("insufficient_4h_data")
        
        # If daily data insufficient, synthesize from 4H (use every 6th candle)
        daily_closes = None
        if len(daily_candles) >= STRATEGY.ema_slow_daily:
            daily_closes = [c["close"] for c in daily_candles]
        else:
            logger.info(f"{symbol}: Using 4H as proxy for daily trend")
            # Take every 6th 4H candle to approximate daily
            daily_closes = [c["close"] for c in fourh_candles[::6]]
            # Ensure we have enough
            if len(daily_closes) < STRATEGY.ema_slow_daily:
                daily_closes = [c["close"] for c in fourh_candles[::4]]  # Every 4th = 16H
        
        # Calculate Daily EMAs
        daily_ema_fast = calculate_ema(daily_closes, STRATEGY.ema_fast_daily)
        daily_ema_slow = calculate_ema(daily_closes, STRATEGY.ema_slow_daily)
        
        # Calculate 4H EMAs
        fourh_closes = [c["close"] for c in fourh_candles]
        fourh_ema_fast = calculate_ema(fourh_closes, STRATEGY.ema_fast_4h)
        fourh_ema_slow = calculate_ema(fourh_closes, STRATEGY.ema_slow_4h)
        
        # Check alignment
        daily_up = is_trending_up(daily_ema_fast, daily_ema_slow)
        daily_down = is_trending_down(daily_ema_fast, daily_ema_slow)
        fourh_up = is_trending_up(fourh_ema_fast, fourh_ema_slow)
        fourh_down = is_trending_down(fourh_ema_fast, fourh_ema_slow)
        
        daily_aligned = daily_up or daily_down
        fourh_aligned = fourh_up or fourh_down
        
        # Determine direction
        if daily_up and fourh_up:
            direction = "up"
            score = 1.0
        elif daily_down and fourh_down:
            direction = "down"
            score = 1.0
        elif (daily_up and fourh_down) or (daily_down and fourh_up):
            direction = "conflicted"
            score = 0.3  # Conflicted alignment, low score
        elif daily_aligned or fourh_aligned:
            # Only one timeframe aligned
            if daily_up or fourh_up:
                direction = "up_weak"
            else:
                direction = "down_weak"
            score = 0.5
        else:
            direction = "neutral"
            score = 0.0
        
        # Hard requirement mode: both must agree
        if STRATEGY.trend_alignment_mode == "hard":
            fully_aligned = (daily_up and fourh_up) or (daily_down and fourh_down)
            if not fully_aligned:
                direction = "neutral"
                score = 0.0
        
        result = {
            "direction": direction,
            "daily_aligned": daily_aligned,
            "fourh_aligned": fourh_aligned,
            "score": score,
            "mode": STRATEGY.trend_alignment_mode,
            "details": {
                "daily": {
                    "ema_fast": round(daily_ema_fast[-1], 2) if daily_ema_fast[-1] == daily_ema_fast[-1] else None,
                    "ema_slow": round(daily_ema_slow[-1], 2) if daily_ema_slow[-1] == daily_ema_slow[-1] else None,
                    "trending_up": daily_up,
                    "trending_down": daily_down,
                },
                "fourh": {
                    "ema_fast": round(fourh_ema_fast[-1], 2) if fourh_ema_fast[-1] == fourh_ema_fast[-1] else None,
                    "ema_slow": round(fourh_ema_slow[-1], 2) if fourh_ema_slow[-1] == fourh_ema_slow[-1] else None,
                    "trending_up": fourh_up,
                    "trending_down": fourh_down,
                }
            }
        }
        
        logger.info(
            f"Trend {symbol}: {direction} | Daily={'UP' if daily_up else 'DOWN' if daily_down else 'NEUTRAL'} | "
            f"4H={'UP' if fourh_up else 'DOWN' if fourh_down else 'NEUTRAL'} | "
            f"Score={score:.1f} | Mode={STRATEGY.trend_alignment_mode}"
        )
        
        return result
    
    def _neutral_result(self, reason: str) -> Dict:
        """Return neutral result with reason."""
        return {
            "direction": "neutral",
            "daily_aligned": False,
            "fourh_aligned": False,
            "score": 0.0,
            "mode": STRATEGY.trend_alignment_mode,
            "details": {
                "daily": {"ema_fast": None, "ema_slow": None, "trending_up": False, "trending_down": False},
                "fourh": {"ema_fast": None, "ema_slow": None, "trending_up": False, "trending_down": False},
            },
            "reason": reason
        }
    
    def is_aligned_for_long(self, trend_result: Dict) -> bool:
        """Quick check if trend supports long positions."""
        return trend_result["direction"] in ("up", "up_weak") and trend_result["score"] > 0
    
    def is_aligned_for_short(self, trend_result: Dict) -> bool:
        """Quick check if trend supports short positions."""
        return trend_result["direction"] in ("down", "down_weak") and trend_result["score"] > 0