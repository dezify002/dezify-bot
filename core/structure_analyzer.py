"""
Layer 3: Market Structure Analyzer.
Detects pullbacks, higher lows/lower highs, CHoCH, liquidity sweeps.
"""

from typing import Dict, List, Optional, Tuple

from core.market_data import MarketData
from utils.helpers import find_swing_highs, find_swing_lows
from utils.logger import get_logger
from config.settings import STRATEGY

logger = get_logger(__name__)


class StructureAnalyzer:
    """
    Layer 3 of the 8-layer architecture.
    Identifies market structure for entry timing.
    """
    
    def __init__(self, market_data: Optional[MarketData] = None):
        self.market_data = market_data or MarketData()
    
    def analyze(self, symbol: str, direction: str) -> Dict:
        """
        Analyze market structure for potential entry.
        
        Args:
            symbol: Trading pair
            direction: "up" or "down" (from trend analysis)
        
        Returns:
            Dict with structure signals and confirmation flags.
        """
        candles = self.market_data.get_candles(
            symbol, "4H", limit=STRATEGY.pullback_lookback * 2
        )
        
        if len(candles) < STRATEGY.pullback_lookback:
            logger.warning(f"{symbol}: Insufficient data for structure analysis")
            return self._empty_result("insufficient_data")
        
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        if direction == "up":
            return self._analyze_long_structure(candles, closes, highs, lows)
        elif direction == "down":
            return self._analyze_short_structure(candles, closes, highs, lows)
        else:
            return self._empty_result("no_direction")
    
    def _analyze_long_structure(
        self,
        candles: List[Dict],
        closes: List[float],
        highs: List[float],
        lows: List[float]
    ) -> Dict:
        """Analyze structure for long entry."""
        # Find swing lows (potential higher lows)
        swing_lows = find_swing_lows(lows, lookback=3)
        
        # Need at least 2 swing lows to compare
        if len(swing_lows) < 2:
            return self._empty_result("no_swing_lows")
        
        # Check for higher low pattern
        recent_swing = swing_lows[-1]
        previous_swing = swing_lows[-2]
        higher_low = recent_swing[1] > previous_swing[1]
        
        # Check for pullback (price came down from recent high)
        recent_high = max(highs[recent_swing[0]:]) if recent_swing[0] < len(highs) else highs[-1]
        pullback_occurred = closes[-1] < recent_high
        
        # Check for structure break (CHoCH)
        # Break above recent swing high = bullish CHoCH
        recent_swing_highs = find_swing_highs(highs, lookback=3)
        structure_break = False
        chooch_level = None
        
        if len(recent_swing_highs) >= 2:
            # Price broke above previous lower high
            prev_lower_high = recent_swing_highs[-2][1]
            if highs[-1] > prev_lower_high:
                structure_break = True
                chooch_level = prev_lower_high
        
        # Check for liquidity sweep
        # Price briefly below previous swing low then reversed
        liquidity_sweep = False
        sweep_level = None
        
        if len(swing_lows) >= 2:
            prev_low = swing_lows[-2][1]
            # Check if any low in recent candles went below prev_low but close recovered
            for i in range(max(0, len(candles) - 5), len(candles)):
                if lows[i] < prev_low and closes[i] > prev_low:
                    liquidity_sweep = True
                    sweep_level = prev_low
                    break
        
        # Rejection candle (optional, testable parameter)
        rejection = False
        if len(candles) >= 2:
            last = candles[-1]
            body = abs(last["close"] - last["open"])
            upper_wick = last["high"] - max(last["close"], last["open"])
            lower_wick = min(last["close"], last["open"]) - last["low"]
            
            # Bullish rejection: long lower wick, body in upper half
            if lower_wick > body * 1.5 and last["close"] > last["open"]:
                rejection = True
        
        # Determine if structure is favorable
        pullback = pullback_occurred
        hl = higher_low
        sb = structure_break if STRATEGY.structure_break_confirmed else True
        ls = liquidity_sweep if STRATEGY.liquidity_sweep_required else True
        rc = rejection if STRATEGY.rejection_candle_required else True
        
        structure_valid = pullback and hl and sb and ls and rc
        
        result = {
            "valid": structure_valid,
            "direction": "long",
            "pullback": pullback,
            "higher_low": hl,
            "structure_break": structure_break,
            "liquidity_sweep": liquidity_sweep,
            "rejection_candle": rejection,
            "details": {
                "recent_swing_low": recent_swing[1] if swing_lows else None,
                "previous_swing_low": previous_swing[1] if len(swing_lows) > 1 else None,
                "chooch_level": chooch_level,
                "sweep_level": sweep_level,
                "recent_high": recent_high,
            }
        }
        
        logger.info(
            f"Structure {symbol} LONG: valid={structure_valid} | "
            f"PB={pullback} | HL={hl} | SB={structure_break} | "
            f"LS={liquidity_sweep} | RC={rejection}"
        )
        
        return result
    
    def _analyze_short_structure(
        self,
        candles: List[Dict],
        closes: List[float],
        highs: List[float],
        lows: List[float]
    ) -> Dict:
        """Analyze structure for short entry."""
        # Find swing highs (potential lower highs)
        swing_highs = find_swing_highs(highs, lookback=3)
        
        if len(swing_highs) < 2:
            return self._empty_result("no_swing_highs")
        
        # Check for lower high pattern
        recent_swing = swing_highs[-1]
        previous_swing = swing_highs[-2]
        lower_high = recent_swing[1] < previous_swing[1]
        
        # Check for pullback (rally from recent low)
        recent_low = min(lows[recent_swing[0]:]) if recent_swing[0] < len(lows) else lows[-1]
        pullback_occurred = closes[-1] > recent_low
        
        # Check for structure break (bearish CHoCH)
        recent_swing_lows = find_swing_lows(lows, lookback=3)
        structure_break = False
        chooch_level = None
        
        if len(recent_swing_lows) >= 2:
            prev_higher_low = recent_swing_lows[-2][1]
            if lows[-1] < prev_higher_low:
                structure_break = True
                chooch_level = prev_higher_low
        
        # Check for liquidity sweep
        liquidity_sweep = False
        sweep_level = None
        
        if len(swing_highs) >= 2:
            prev_high = swing_highs[-2][1]
            for i in range(max(0, len(candles) - 5), len(candles)):
                if highs[i] > prev_high and closes[i] < prev_high:
                    liquidity_sweep = True
                    sweep_level = prev_high
                    break
        
        # Rejection candle (bearish)
        rejection = False
        if len(candles) >= 2:
            last = candles[-1]
            body = abs(last["close"] - last["open"])
            upper_wick = last["high"] - max(last["close"], last["open"])
            
            # Bearish rejection: long upper wick, body in lower half
            if upper_wick > body * 1.5 and last["close"] < last["open"]:
                rejection = True
        
        # Determine validity
        pullback = pullback_occurred
        lh = lower_high
        sb = structure_break if STRATEGY.structure_break_confirmed else True
        ls = liquidity_sweep if STRATEGY.liquidity_sweep_required else True
        rc = rejection if STRATEGY.rejection_candle_required else True
        
        structure_valid = pullback and lh and sb and ls and rc
        
        result = {
            "valid": structure_valid,
            "direction": "short",
            "pullback": pullback,
            "lower_high": lh,
            "structure_break": structure_break,
            "liquidity_sweep": liquidity_sweep,
            "rejection_candle": rejection,
            "details": {
                "recent_swing_high": recent_swing[1] if swing_highs else None,
                "previous_swing_high": previous_swing[1] if len(swing_highs) > 1 else None,
                "chooch_level": chooch_level,
                "sweep_level": sweep_level,
                "recent_low": recent_low,
            }
        }
        
        logger.info(
            f"Structure {symbol} SHORT: valid={structure_valid} | "
            f"PB={pullback} | LH={lh} | SB={structure_break} | "
            f"LS={liquidity_sweep} | RC={rejection}"
        )
        
        return result
    
    def _empty_result(self, reason: str) -> Dict:
        """Return empty/invalid structure result."""
        return {
            "valid": False,
            "direction": "none",
            "pullback": False,
            "higher_low": False,
            "lower_high": False,
            "structure_break": False,
            "liquidity_sweep": False,
            "rejection_candle": False,
            "reason": reason,
            "details": {}
        }