"""
Layer 5: Risk Manager.
Position sizing, drawdown limits, correlation bucketing, kill switches.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from core.market_data import MarketData
from data.database import Database
from utils.helpers import calculate_atr, safe_divide
from utils.logger import get_logger
from config.settings import RISK, UNIVERSE

logger = get_logger(__name__)


@dataclass
class PositionSizing:
    """Result of position size calculation."""
    size: float              # Position size in base asset
    position_value: float    # Position value in USD
    leverage: int            # Leverage used
    stop_loss_price: float   # Stop loss price
    take_profit_price: float # Take profit price
    risk_amount: float       # Dollar amount at risk
    risk_pct: float          # Risk as % of account
    risk_reward: float       # R:R ratio


class RiskManager:
    """
    Layer 5 of the 8-layer architecture.
    Manages all risk parameters and enforces limits.
    """
    
    def __init__(
        self,
        market_data: Optional[MarketData] = None,
        database: Optional[Database] = None
    ):
        self.market_data = market_data or MarketData()
        self.db = database or Database()
        
        # Track state
        self.account_equity: float = 0.0
        self.daily_pnl: float = 0.0
        self.weekly_pnl: float = 0.0
        self.peak_equity: float = 0.0
        self.daily_start_equity: float = 0.0
        self.weekly_start_equity: float = 0.0
        self.last_reset_date: Optional[datetime] = None
        
        # Position tracking
        self.open_positions: Dict[str, Dict] = {}  # symbol -> position info
        self.correlation_buckets: Dict[str, List[str]] = {
            bucket: [] for bucket in RISK.correlation_buckets
        }
    
    def update_account_state(self, equity: float):
        """Update account equity and check drawdown limits."""
        self.account_equity = equity
        
        # Track peak
        if equity > self.peak_equity:
            self.peak_equity = equity
        
        # Reset daily/weekly tracking if needed
        now = datetime.utcnow()
        if self.last_reset_date is None:
            self.daily_start_equity = equity
            self.weekly_start_equity = equity
            self.last_reset_date = now
        
        # Check if we need to reset daily/weekly counters
        if now.date() != self.last_reset_date.date():
            self.daily_pnl = 0.0
            self.daily_start_equity = equity
            self.last_reset_date = now
        
        if now.isocalendar()[1] != self.last_reset_date.isocalendar()[1]:
            self.weekly_pnl = 0.0
            self.weekly_start_equity = equity
    
    def can_trade(self) -> Tuple[bool, str]:
        """
        Master kill switch check.
        Returns (can_trade, reason).
        """
        if self.account_equity <= 0:
            return False, "zero_equity"
        
        # Daily drawdown check
        daily_dd = safe_divide(
            self.daily_start_equity - self.account_equity,
            self.daily_start_equity,
            default=0.0
        )
        if daily_dd >= RISK.max_daily_drawdown:
            self.db.log_event(
                "KILL_SWITCH",
                f"Daily drawdown limit hit: {daily_dd*100:.2f}%",
                {"limit": RISK.max_daily_drawdown, "current": daily_dd}
            )
            return False, f"daily_drawdown_{daily_dd*100:.1f}%"
        
        # Weekly drawdown check
        weekly_dd = safe_divide(
            self.weekly_start_equity - self.account_equity,
            self.weekly_start_equity,
            default=0.0
        )
        if weekly_dd >= RISK.max_weekly_drawdown:
            self.db.log_event(
                "KILL_SWITCH",
                f"Weekly drawdown limit hit: {weekly_dd*100:.2f}%",
                {"limit": RISK.max_weekly_drawdown, "current": weekly_dd}
            )
            return False, f"weekly_drawdown_{weekly_dd*100:.1f}%"
        
        # Max concurrent positions
        if len(self.open_positions) >= RISK.max_concurrent_positions:
            return False, "max_positions"
        
        return True, "ok"
    
    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        direction: str,
        account_equity: float
    ) -> Optional[PositionSizing]:
        """
        Calculate position size based on ATR and risk parameters.
        
        Args:
            symbol: Trading pair
            entry_price: Planned entry price
            direction: "long" or "short"
            account_equity: Current account equity in USD
        
        Returns:
            PositionSizing object or None if calculation fails.
        """
        # Get ATR for stop loss distance
        candles = self.market_data.get_candles(symbol, "4H", limit=50)
        if len(candles) < 20:
            logger.warning(f"{symbol}: Insufficient data for position sizing")
            return None
        
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        closes = [c["close"] for c in candles]
        
        atr = calculate_atr(highs, lows, closes, period=14)
        if atr <= 0:
            return None
        
        # Calculate stop distance
        stop_distance = atr * RISK.atr_stop_multiplier
        
        # Enforce min/max stop distance
        stop_distance_pct = stop_distance / entry_price
        min_stop = entry_price * RISK.min_stop_distance_pct
        max_stop = entry_price * RISK.max_stop_distance_pct
        
        stop_distance = max(min(stop_distance, max_stop), min_stop)
        
        # Calculate stop loss price
        if direction == "long":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + (stop_distance * 2)  # 1:2 R:R
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - (stop_distance * 2)
        
        # Risk amount
        risk_amount = account_equity * RISK.max_risk_per_trade
        risk_pct = RISK.max_risk_per_trade
        
        # Position size in base asset
        # risk_amount = position_size * (stop_distance / entry_price) * leverage
        # Solving for position_size:
        position_value = risk_amount / (stop_distance / entry_price)
        size = position_value / entry_price
        
        # Apply leverage
        leverage = min(RISK.default_leverage, RISK.max_leverage)
        margin_required = position_value / leverage
        
        # Check if we have enough equity
        if margin_required > account_equity * 0.5:  # Don't use more than 50% margin
            logger.warning(f"{symbol}: Position too large for account")
            return None
        
        # R:R ratio
        reward = abs(take_profit - entry_price)
        risk_reward = reward / stop_distance if stop_distance > 0 else 0
        
        return PositionSizing(
            size=round(size, 8),
            position_value=round(position_value, 2),
            leverage=leverage,
            stop_loss_price=round(stop_loss, 4),
            take_profit_price=round(take_profit, 4),
            risk_amount=round(risk_amount, 2),
            risk_pct=round(risk_pct, 4),
            risk_reward=round(risk_reward, 2),
        )
    
    def check_correlation_limit(self, symbol: str, bucket: str) -> bool:
        """
        Check if adding this symbol would exceed correlation bucket limit.
        
        Args:
            symbol: Trading pair
            bucket: Correlation bucket (e.g., "layer1", "meme", "defi")
        
        Returns:
            True if within limit, False if bucket is full.
        """
        current_in_bucket = len(self.correlation_buckets.get(bucket, []))
        if current_in_bucket >= RISK.max_positions_per_correlation_bucket:
            logger.info(
                f"Correlation limit hit for {bucket}: "
                f"{current_in_bucket}/{RISK.max_positions_per_correlation_bucket}"
            )
            return False
        return True
    
    def register_position(self, symbol: str, bucket: str, position_info: Dict):
        """Register an open position for tracking."""
        self.open_positions[symbol] = position_info
        if bucket in self.correlation_buckets:
            if symbol not in self.correlation_buckets[bucket]:
                self.correlation_buckets[bucket].append(symbol)
        logger.info(f"Position registered: {symbol} in bucket {bucket}")
    
    def close_position(self, symbol: str, pnl: float):
        """Remove position from tracking and update P&L."""
        if symbol in self.open_positions:
            info = self.open_positions.pop(symbol)
            bucket = info.get("bucket", "unknown")
            if bucket in self.correlation_buckets and symbol in self.correlation_buckets[bucket]:
                self.correlation_buckets[bucket].remove(symbol)
        
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        logger.info(f"Position closed: {symbol} | PnL: ${pnl:.2f}")
    
    def get_open_position_count(self) -> int:
        """Current number of open positions."""
        return len(self.open_positions)
    
    def get_drawdown_stats(self) -> Dict:
        """Get current drawdown statistics."""
        if self.peak_equity <= 0:
            return {"current_dd_pct": 0, "daily_dd_pct": 0, "weekly_dd_pct": 0}
        
        current_dd = (self.peak_equity - self.account_equity) / self.peak_equity
        daily_dd = safe_divide(
            self.daily_start_equity - self.account_equity,
            self.daily_start_equity,
            default=0.0
        )
        weekly_dd = safe_divide(
            self.weekly_start_equity - self.account_equity,
            self.weekly_start_equity,
            default=0.0
        )
        
        return {
            "current_dd_pct": round(current_dd, 4),
            "daily_dd_pct": round(daily_dd, 4),
            "weekly_dd_pct": round(weekly_dd, 4),
            "peak_equity": self.peak_equity,
            "current_equity": self.account_equity,
        }
    
    def assign_correlation_bucket(self, symbol: str) -> str:
        """
        Assign a symbol to a correlation bucket.
        In production, you'd use a mapping or API.
        """
        symbol_upper = symbol.upper()
        
        # Simple heuristic mapping
        if any(x in symbol_upper for x in ["BTC", "ETH"]):
            return "layer1"
        elif any(x in symbol_upper for x in ["DOGE", "SHIB", "PEPE", "FLOKI"]):
            return "meme"
        elif any(x in symbol_upper for x in ["UNI", "AAVE", "COMP", "MKR", "CRV"]):
            return "defi"
        elif any(x in symbol_upper for x in ["FET", "RNDR", "TAO", "WLD", "NEAR"]):
            return "ai"
        elif any(x in symbol_upper for x in ["ONDO", "CFG", "POLYX"]):
            return "rwa"
        else:
            return "infra"