"""
Layer 6: Position Sizer.
Calculates optimal position size based on risk parameters.
"""

from typing import Dict

from utils.logger import get_logger
from config.settings import RISK

logger = get_logger(__name__)


class PositionSizer:
    """
    Layer 6 of the 8-layer architecture.
    Determines position size, leverage, and risk amount.
    """
    
    def __init__(self):
        pass
    
    def calculate(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        direction: str,
        atr: float = 0
    ) -> Dict:
        """
        Calculate position sizing parameters.
        
        Args:
            equity: Total account equity in USD
            entry_price: Planned entry price
            stop_loss: Stop loss price
            direction: "long" or "short"
            atr: Average True Range (optional)
        
        Returns:
            Dict with:
                - size: float (position size in base asset)
                - value: float (position value in USD)
                - leverage: int
                - risk_pct: float (risk as % of equity)
                - risk_usd: float (risk in USD)
        """
        if equity <= 0 or entry_price <= 0:
            logger.error("Invalid equity or entry price for position sizing")
            return {
                "size": 0,
                "value": 0,
                "leverage": 1,
                "risk_pct": 0,
                "risk_usd": 0,
            }
        
        # Risk amount in USD
        risk_usd = equity * RISK.max_account_risk_per_trade
        
        # Risk distance per unit
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance <= 0:
            logger.error("Invalid stop loss distance")
            return {
                "size": 0,
                "value": 0,
                "leverage": 1,
                "risk_pct": 0,
                "risk_usd": 0,
            }
        
        # Position size in base asset
        position_size = risk_usd / risk_distance
        
        # Position value in USD
        position_value = position_size * entry_price
        
        # Determine leverage
        # Leverage = position_value / equity, capped at max
        raw_leverage = position_value / equity if equity > 0 else 1
        leverage = max(RISK.min_leverage, min(int(raw_leverage) + 1, RISK.max_leverage))
        
        # Recalculate with capped leverage if needed
        if raw_leverage > RISK.max_leverage:
            position_value = equity * RISK.max_leverage
            position_size = position_value / entry_price
            risk_usd = position_size * risk_distance
            leverage = RISK.max_leverage
        
        risk_pct = risk_usd / equity if equity > 0 else 0
        
        result = {
            "size": round(position_size, 6),
            "value": round(position_value, 2),
            "leverage": leverage,
            "risk_pct": round(risk_pct, 4),
            "risk_usd": round(risk_usd, 2),
        }
        
        logger.info(
            f"Position size: ${position_value:,.2f} | "
            f"Size: {position_size:.6f} | "
            f"Leverage: {leverage}x | "
            f"Risk: ${risk_usd:.2f} ({risk_pct*100:.2f}%)"
        )
        
        return result