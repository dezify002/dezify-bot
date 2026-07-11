"""
Layer 4: Entry Trigger.
Confirms pullback completion and defines precise entry/stop/take-profit levels.
"""

from typing import Dict, Optional

from core.market_data import MarketData
from utils.helpers import calculate_atr
from utils.logger import get_logger
from config.settings import STRATEGY, RISK

logger = get_logger(__name__)


class EntryTrigger:
    """
    Layer 4 of the 8-layer architecture.
    Validates entry conditions and calculates exact order parameters.
    """
    
    def __init__(self, market_data: Optional[MarketData] = None):
        self.market_data = market_data or MarketData()
    
    def check(self, symbol: str, direction: str, pullback: Dict) -> Dict:
        """
        Check if entry conditions are met and calculate order levels.
        
        Args:
            symbol: Trading pair
            direction: "long" or "short"
            pullback: Result from PullbackDetector
        
        Returns:
            Dict with:
                - triggered: bool
                - entry_price: float
                - stop_loss: float
                - take_profit: float
                - risk_reward: float
        """
        if not pullback.get("valid"):
            logger.debug(f"{symbol}: No entry - pullback not valid")
            return {"triggered": False}
        
        current_price = self.market_data.get_latest_price(symbol)
        if not current_price:
            logger.warning(f"{symbol}: Cannot get current price")
            return {"triggered": False}
        
        atr = pullback.get("atr", 0)
        if atr <= 0:
            logger.warning(f"{symbol}: Invalid ATR ({atr})")
            return {"triggered": False}
        
        # Calculate entry, stop, and take-profit
        if direction == "long":
            entry_price = current_price
            stop_loss = pullback.get("stop_loss", current_price - atr * 1.5)
            
            # Ensure stop is below entry
            if stop_loss >= entry_price:
                stop_loss = entry_price - atr * 1.5
            
            # Risk distance
            risk_distance = entry_price - stop_loss
            
            # Take profit at 2R minimum, 3R target
            take_profit = entry_price + risk_distance * RISK.risk_reward_target
            
        else:  # short
            entry_price = current_price
            stop_loss = pullback.get("stop_loss", current_price + atr * 1.5)
            
            # Ensure stop is above entry
            if stop_loss <= entry_price:
                stop_loss = entry_price + atr * 1.5
            
            risk_distance = stop_loss - entry_price
            take_profit = entry_price - risk_distance * RISK.risk_reward_target
        
        # Validate risk/reward
        risk_reward = abs(take_profit - entry_price) / abs(entry_price - stop_loss)
        
        # Check stop distance as % of price
        stop_pct = abs(entry_price - stop_loss) / entry_price
        
        # LOG EVERYTHING BEFORE REJECTION
        logger.info(
            f"{symbol} {direction} | Entry={entry_price:.4f} | "
            f"SL={stop_loss:.4f} | TP={take_profit:.4f} | "
            f"Risk=${risk_distance:.4f} | RR={risk_reward:.2f} | "
            f"Stop%={stop_pct:.2%} | ATR={atr:.4f}"
        )
        
        # Check R:R
        if risk_reward < RISK.risk_reward_min:
            logger.info(
                f"❌ {symbol} REJECTED: R:R {risk_reward:.2f} < minimum {RISK.risk_reward_min}"
            )
            return {"triggered": False}
        
        # Check stop distance
        if stop_pct > RISK.max_sl_pct:
            logger.info(
                f"❌ {symbol} REJECTED: Stop distance {stop_pct:.2%} > max {RISK.max_sl_pct:.2%}"
            )
            return {"triggered": False}
        
        # ALL CHECKS PASSED
        result = {
            "triggered": True,
            "entry_price": round(entry_price, 4),
            "stop_loss": round(stop_loss, 4),
            "take_profit": round(take_profit, 4),
            "risk_reward": round(risk_reward, 2),
            "risk_distance": round(risk_distance, 4),
            "atr": atr,
        }
        
        logger.info(
            f"✅ ENTRY TRIGGERED {symbol} {direction}: "
            f"Entry={entry_price:.2f} | SL={stop_loss:.2f} | "
            f"TP={take_profit:.2f} | R:R={risk_reward:.1f}"
        )
        
        return result