"""
Layer 3: Pullback Detector.
Identifies valid pullbacks within a trending market.
"""

from typing import Dict, Optional

from core.market_data import MarketData
from utils.helpers import calculate_atr
from utils.logger import get_logger
from config.settings import STRATEGY, RISK

logger = get_logger(__name__)


class PullbackDetector:
    """
    Layer 3 of the 8-layer architecture.
    Detects price pullbacks to EMA or support/resistance zones.
    """
    
    def __init__(self, market_data: Optional[MarketData] = None):
        self.market_data = market_data or MarketData()
    
    def detect(self, symbol: str, direction: str) -> Dict:
        """
        Detect if a valid pullback is present.
        
        Args:
            symbol: Trading pair
            direction: "long" or "short" (from trend analysis)
        
        Returns:
            Dict with:
                - valid: bool
                - depth: float (pullback depth as decimal)
                - entry_zone: tuple (low, high)
                - stop_loss: float
                - atr: float
        """
        # Fetch recent 4H candles
        candles = self.market_data.get_candles(symbol, "4H", limit=50)
        
        if len(candles) < 20:
            logger.warning(f"{symbol}: Insufficient data for pullback detection")
            return {"valid": False}
        
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        current_price = closes[-1]
        
        # Calculate ATR
        atr = calculate_atr(highs, lows, closes, period=14)
        
        # Find recent swing high/low
        if direction == "long":
            # For longs: pullback from recent high
            recent_high = max(highs[-10:])
            pullback_depth = (recent_high - current_price) / recent_high
            
            # Valid pullback: price dipped but not too much
            min_depth = STRATEGY.pullback_min_depth
            max_depth = STRATEGY.pullback_max_depth
            
            valid = min_depth <= pullback_depth <= max_depth
            
            # Entry zone: near current price or EMA
            entry_zone = (current_price - atr * 0.5, current_price + atr * 0.2)
            stop_loss = current_price - atr * RISK.atr_multiplier_sl
            
        else:
            # For shorts: pullback from recent low
            recent_low = min(lows[-10:])
            pullback_depth = (current_price - recent_low) / recent_low
            
            min_depth = STRATEGY.pullback_min_depth
            max_depth = STRATEGY.pullback_max_depth
            
            valid = min_depth <= pullback_depth <= max_depth
            
            entry_zone = (current_price - atr * 0.2, current_price + atr * 0.5)
            stop_loss = current_price + atr * RISK.atr_multiplier_sl
        
        result = {
            "valid": valid,
            "depth": round(pullback_depth, 4),
            "entry_zone": entry_zone,
            "stop_loss": round(stop_loss, 4),
            "atr": round(atr, 4),
            "current_price": current_price,
        }
        
        if valid:
            logger.info(
                f"Pullback {symbol} {direction}: depth={pullback_depth:.2%} | "
                f"Entry: {entry_zone[0]:.2f}-{entry_zone[1]:.2f} | "
                f"SL: {stop_loss:.2f}"
            )
        else:
            logger.debug(
                f"No pullback {symbol}: depth={pullback_depth:.2%} "
                f"(need {min_depth:.2%}-{max_depth:.2%})"
            )
        
        return result