"""
Layer 1: Market Regime Detector.
Determines if the market is worth trading before any signal is generated.
"""

from typing import Dict, List, Optional

from core.market_data import MarketData
from utils.helpers import calculate_adx, calculate_atr
from utils.logger import get_logger
from config.settings import STRATEGY

logger = get_logger(__name__)


class RegimeDetector:
    """
    Layer 1 of the 8-layer architecture.
    Filters: ADX > threshold, ATR sufficient, Volume sufficient.
    """
    
    def __init__(self, market_data: Optional[MarketData] = None):
        self.market_data = market_data or MarketData()
    
    def evaluate(self, symbol: str) -> Dict:
        """
        Evaluate if market regime permits trading.
        
        Returns:
            Dict with:
                - tradable: bool (master switch)
                - regime: str ("trending", "ranging", "inactive")
                - adx: float
                - atr: float
                - atr_pct: float (ATR as % of price)
                - volume_24h: float
                - checks: dict of individual pass/fail
        """
        # Fetch data
        candles = self.market_data.get_candles(symbol, "4H", limit=STRATEGY.adx_period * 3)
        
        if len(candles) < STRATEGY.adx_period * 2:
            logger.warning(f"{symbol}: Insufficient candle data ({len(candles)} bars)")
            return self._result(False, "insufficient_data")
        
        # Extract OHLCV
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        closes = [c["close"] for c in candles]
        current_price = closes[-1]
        
        # Calculate ADX
        adx = calculate_adx(highs, lows, closes, period=STRATEGY.adx_period)
        
        # Calculate ATR
        atr = calculate_atr(highs, lows, closes, period=STRATEGY.atr_period)
        atr_pct = (atr / current_price * 100) if current_price > 0 else 0
        
        # Get 24h volume
        volume_24h = self.market_data.get_volume_24h(symbol)
        
        # Check ATR sufficiency (ATR must be > min threshold % of price)
        min_atr_pct = STRATEGY.min_atr_multiplier
        atr_sufficient = atr_pct >= min_atr_pct
        
        # Check ADX
        adx_sufficient = adx >= STRATEGY.adx_threshold
        
        # Check volume
        volume_sufficient = volume_24h >= STRATEGY.min_volume_24h
        
        # Determine regime
        if adx_sufficient and atr_sufficient and volume_sufficient:
            regime = "trending"
            tradable = True
        elif not adx_sufficient and atr_sufficient and volume_sufficient:
            regime = "ranging"
            tradable = False
        else:
            regime = "inactive"
            tradable = False
        
        result = {
            "tradable": tradable,
            "regime": regime,
            "adx": round(adx, 2),
            "atr": round(atr, 4),
            "atr_pct": round(atr_pct, 4),
            "volume_24h": round(volume_24h, 2),
            "current_price": current_price,
            "checks": {
                "adx_above_threshold": adx_sufficient,
                "atr_sufficient": atr_sufficient,
                "volume_sufficient": volume_sufficient,
            }
        }
        
        logger.info(
            f"Regime check {symbol}: {regime} | ADX={adx:.1f} | ATR%={atr_pct:.2f}% | "
            f"Vol=${volume_24h:,.0f} | Tradable={tradable}"
        )
        
        return result
    
    def _result(self, tradable: bool, reason: str) -> Dict:
        """Return a failed result with defaults."""
        return {
            "tradable": tradable,
            "regime": reason,
            "adx": 0.0,
            "atr": 0.0,
            "atr_pct": 0.0,
            "volume_24h": 0.0,
            "current_price": 0.0,
            "checks": {
                "adx_above_threshold": False,
                "atr_sufficient": False,
                "volume_sufficient": False,
            }
        }
    
    def get_regime_label(self, symbol: str) -> str:
        """
        Get a descriptive regime label for analytics.
        Used for the regime-specific performance breakdown.
        """
        eval_result = self.evaluate(symbol)
        
        if not eval_result["tradable"]:
            if eval_result["checks"]["adx_above_threshold"]:
                return "low_volatility"
            return "sideways"
        
        # Trending - determine direction from price action
        candles = self.market_data.get_candles(symbol, "1D", limit=20)
        if len(candles) >= 2:
            recent = candles[-1]["close"]
            older = candles[0]["close"]
            if recent > older * 1.05:  # 5% up over lookback
                return "strong_uptrend"
            elif recent < older * 0.95:  # 5% down
                return "strong_downtrend"
        
        return "trending_neutral"