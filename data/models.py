"""
Data models for trade records using Python dataclasses.
These define the schema for what gets stored in SQLite.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, List, Any
import json


@dataclass
class Signal:
    """
    A validated trade signal ready for execution.
    Contains all parameters needed to place an order.
    """
    symbol: str
    direction: str  # "long" or "short"
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    position_value: float
    leverage: int
    risk_pct: float
    checklist: "TradeChecklist" = field(default_factory=lambda: TradeChecklist())
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def risk_reward(self) -> float:
        """Calculate risk:reward ratio."""
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit - self.entry_price)
        return reward / risk if risk > 0 else 0.0


@dataclass
class TradeChecklist:
    """
    Layer-by-layer checklist for every trade.
    This is the explainability schema from your strategy doc.
    """
    # Layer 1: Market Regime
    adx_above_threshold: bool = False
    adx_value: float = 0.0
    atr_sufficient: bool = False
    atr_value: float = 0.0
    volume_sufficient: bool = False
    volume_24h: float = 0.0
    
    # Layer 2: Trend
    daily_ema_aligned: bool = False
    fourh_ema_aligned: bool = False
    trend_direction: str = ""  # "up", "down", "neutral"
    trend_score: float = 0.0
    
    # Layer 3: Market Structure
    pullback_confirmed: bool = False
    pullback_depth: Optional[float] = None
    higher_low_formed: bool = False
    lower_high_formed: bool = False
    structure_break: bool = False
    liquidity_sweep: bool = False
    rejection_candle: bool = False
    
    # Layer 4: Order Flow
    volume_expansion: bool = False
    volume_ratio: float = 0.0
    open_interest_increasing: bool = False
    oi_change_pct: float = 0.0
    funding_rate: float = 0.0
    funding_context: str = ""  # "normal", "elevated", "extreme"
    
    # Layer 5: Risk
    position_size: float = 0.0
    position_value: float = 0.0
    leverage: int = 1
    risk_pct: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    risk_reward_ratio: float = 0.0
    correlation_limit_passed: bool = False
    kill_switch_inactive: bool = True
    
    # Layer 6: Execution
    entry_triggered: bool = False
    entry_price: Optional[float] = None
    order_type: str = ""  # "limit", "market"
    limit_price: Optional[float] = None
    slippage_pct: float = 0.0
    
    # Layer 7: Trade Management
    break_even_enabled: bool = False
    trailing_stop_enabled: bool = False
    exit_reason: str = ""  # "target", "stop", "structure_break", "trailing", "manual"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeChecklist":
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TradeRecord:
    """
    Complete trade record for analytics and review.
    One row per trade in the database.
    """
    # Identification
    trade_id: str
    symbol: str
    direction: str  # "long" or "short"
    
    # Timing
    entry_time: datetime
    exit_time: Optional[datetime] = None
    holding_period_hours: float = 0.0
    
    # Prices
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    
    # Size & Risk
    position_size: float = 0.0  # In base asset units
    position_value_usd: float = 0.0
    leverage: int = 1
    risk_pct: float = 0.0  # % of account risked
    
    # P&L
    realized_pnl: float = 0.0
    realized_pnl_pct: float = 0.0
    r_multiple: float = 0.0
    
    # Costs
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    total_fees: float = 0.0
    funding_paid: float = 0.0
    slippage_entry: float = 0.0
    slippage_exit: float = 0.0
    
    # Market Context (at entry)
    market_regime: str = ""  # "strong_uptrend", "strong_downtrend", "sideways", "high_vol", "low_vol"
    adx_at_entry: float = 0.0
    atr_at_entry: float = 0.0
    
    # Full Checklist
    checklist: TradeChecklist = field(default_factory=TradeChecklist)
    
    # Analytics
    expected_vs_actual: str = ""  # "met", "exceeded", "underperformed"
    notes: str = ""
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        data = asdict(self)
        data["checklist"] = self.checklist.to_dict()
        data["entry_time"] = self.entry_time.isoformat() if self.entry_time else None
        data["exit_time"] = self.exit_time.isoformat() if self.exit_time else None
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeRecord":
        """Create from dictionary (e.g., from database)."""
        checklist_data = data.pop("checklist", {})
        checklist = TradeChecklist.from_dict(checklist_data)
        
        # Parse datetime strings
        for field_name in ["entry_time", "exit_time", "created_at", "updated_at"]:
            if field_name in data and isinstance(data[field_name], str):
                data[field_name] = datetime.fromisoformat(data[field_name])
        
        data["checklist"] = checklist
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    def is_winner(self) -> bool:
        """True if trade was profitable."""
        return self.realized_pnl > 0
    
    def is_closed(self) -> bool:
        """True if trade has been exited."""
        return self.exit_time is not None


@dataclass
class DailyPerformance:
    """
    Daily aggregate performance record.
    Used for drawdown tracking and review cycles.
    """
    date: str  # YYYY-MM-DD
    starting_equity: float = 0.0
    ending_equity: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    total_fees: float = 0.0
    total_funding: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    peak_equity: float = 0.0
    sharpe_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades
    
    @property
    def avg_winner(self) -> float:
        if self.winning_trades == 0:
            return 0.0
        return self.gross_profit / self.winning_trades
    
    @property
    def avg_loser(self) -> float:
        if self.losing_trades == 0:
            return 0.0
        return self.gross_loss / self.losing_trades


@dataclass
class RegimeLabel:
    """
    Market regime label stamped at signal time (not reconstructed).
    Prevents hindsight bias in analytics.
    """
    timestamp: datetime
    symbol: str
    regime: str  # "strong_uptrend", "strong_downtrend", "sideways", "high_vol", "low_vol"
    adx: float = 0.0
    atr_pct: float = 0.0
    volume_vs_avg: float = 0.0
    daily_trend: str = ""  # "up", "down", "neutral"
    fourh_trend: str = ""  # "up", "down", "neutral"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "regime": self.regime,
            "adx": self.adx,
            "atr_pct": self.atr_pct,
            "volume_vs_avg": self.volume_vs_avg,
            "daily_trend": self.daily_trend,
            "fourh_trend": self.fourh_trend,
        }