"""
Layer 7: Trade Manager.
Manages open positions: break-even, trailing stops, exits on structure change.
"""

from typing import Dict, List, Optional

from core.market_data import MarketData
from core.execution_engine import ExecutionEngine
from data.database import Database
from data.models import TradeRecord
from utils.logger import get_logger
from config.settings import TRADE_MGMT

logger = get_logger(__name__)


class TradeManager:
    """
    Layer 7 of the 8-layer architecture.
    Manages lifecycle of open positions after entry.
    """
    
    def __init__(
        self,
        market_data: Optional[MarketData] = None,
        execution: Optional[ExecutionEngine] = None,
        database: Optional[Database] = None
    ):
        self.market_data = market_data or MarketData()
        self.execution = execution or ExecutionEngine()
        self.db = database or Database()
        
        # Track active positions in memory
        self.active_trades: Dict[str, TradeRecord] = {}
    
    def register_trade(self, trade: TradeRecord):
        """Register a newly opened trade for management."""
        self.active_trades[trade.symbol] = trade
        logger.info(
            f"Trade registered for management: {trade.symbol} | "
            f"Entry: {trade.entry_price} | SL: {trade.stop_loss_price} | "
            f"TP: {trade.take_profit_price}"
        )
    
    def update_all_positions(self) -> List[Dict]:
        """
        Check all active positions and execute exits if conditions met.
        Called on each bar/cycle.
        
        Returns:
            List of exit results.
        """
        exits = []
        symbols_to_remove = []
        
        for symbol, trade in self.active_trades.items():
            result = self._check_position(trade)
            
            if result["should_exit"]:
                exit_result = self._execute_exit(trade, result["reason"])
                exits.append(exit_result)
                symbols_to_remove.append(symbol)
        
        # Clean up closed positions
        for symbol in symbols_to_remove:
            self.active_trades.pop(symbol, None)
        
        return exits
    
    def _check_position(self, trade: TradeRecord) -> Dict:
        """
        Check if a position should be exited.
        
        Evaluates in order:
        1. Stop loss hit
        2. Take profit hit (fixed target)
        3. Trailing stop (if enabled and activated)
        4. Break-even (if enabled and activated)
        5. Market structure change
        """
        symbol = trade.symbol
        direction = trade.direction
        entry = trade.entry_price
        sl = trade.stop_loss_price
        tp = trade.take_profit_price
        
        # Get current price
        current = self.market_data.get_latest_price(symbol)
        if not current:
            return {"should_exit": False, "reason": "no_price"}
        
        # Calculate current R multiple
        if direction == "long":
            price_move = current - entry
            stop_distance = entry - sl
        else:
            price_move = entry - current
            stop_distance = sl - entry
        
        r_multiple = price_move / stop_distance if stop_distance != 0 else 0
        
        # 1. Stop loss hit
        if direction == "long" and current <= sl:
            return {"should_exit": True, "reason": "stop_loss", "price": current, "r": r_multiple}
        if direction == "short" and current >= sl:
            return {"should_exit": True, "reason": "stop_loss", "price": current, "r": r_multiple}
        
        # 2. Take profit hit (fixed target)
        if tp:
            if direction == "long" and current >= tp:
                return {"should_exit": True, "reason": "take_profit", "price": current, "r": r_multiple}
            if direction == "short" and current <= tp:
                return {"should_exit": True, "reason": "take_profit", "price": current, "r": r_multiple}
        
        # 3. Trailing stop (only if enabled AND backtest-validated)
        if TRADE_MGMT.trailing_stop_enabled and r_multiple >= TRADE_MGMT.trailing_stop_activation_r:
            # Calculate trailing stop level
            # For simplicity: entry + (R * stop_distance) - (trailing_distance)
            if direction == "long":
                trail_distance = stop_distance * TRADE_MGMT.trailing_stop_distance_atr
                trail_stop = current - trail_distance
                # Only move stop up, never down
                if trail_stop > sl:
                    # Check if price reversed to trail stop
                    # (In real implementation, you'd track highest price since entry)
                    pass  # Placeholder for trailing logic
            else:
                trail_distance = stop_distance * TRADE_MGMT.trailing_stop_distance_atr
                trail_stop = current + trail_distance
        
        # 4. Break-even (only if enabled AND backtest-validated)
        if TRADE_MGMT.break_even_enabled and r_multiple >= TRADE_MGMT.break_even_trigger_r:
            # Move stop to entry
            if direction == "long" and sl < entry:
                # Update stop to entry (break-even)
                logger.info(f"{symbol}: Moving stop to break-even at {entry}")
                trade.stop_loss_price = entry
                # In live trading, you'd send a modify order
            elif direction == "short" and sl > entry:
                trade.stop_loss_price = entry
        
        # 5. Market structure change (exit on CHoCH against position)
        if TRADE_MGMT.exit_on_structure_break:
            structure_changed = self._check_structure_reversal(symbol, direction)
            if structure_changed:
                return {
                    "should_exit": True,
                    "reason": "structure_break",
                    "price": current,
                    "r": r_multiple
                }
        
        return {"should_exit": False, "reason": "hold", "r": r_multiple}
    
    def _check_structure_reversal(self, symbol: str, direction: str) -> bool:
        """
        Check if market structure has reversed against our position.
        Long: price breaks below recent higher low
        Short: price breaks above recent lower high
        """
        candles = self.market_data.get_candles(symbol, "4H", limit=20)
        if len(candles) < 10:
            return False
        
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        current = candles[-1]["close"]
        
        if direction == "long":
            # Check if we broke below recent swing low (higher low violated)
            recent_lows = []
            for i in range(1, len(lows) - 1):
                if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                    recent_lows.append(lows[i])
            
            if len(recent_lows) >= 2:
                # Higher low pattern broken
                if current < recent_lows[-1]:
                    return True
        
        else:  # short
            # Check if we broke above recent swing high (lower high violated)
            recent_highs = []
            for i in range(1, len(highs) - 1):
                if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                    recent_highs.append(highs[i])
            
            if len(recent_highs) >= 2:
                if current > recent_highs[-1]:
                    return True
        
        return False
    
    def _execute_exit(self, trade: TradeRecord, exit_data: Dict) -> Dict:
        """Execute the exit and update records."""
        reason = exit_data["reason"]
        exit_price = exit_data["price"]
        
        # Calculate P&L
        if trade.direction == "long":
            pnl = (exit_price - trade.entry_price) * trade.position_size
        else:
            pnl = (trade.entry_price - exit_price) * trade.position_size
        
        pnl_pct = pnl / trade.position_value_usd if trade.position_value_usd > 0 else 0
        r_multiple = exit_data.get("r", 0)
        
        # Execute via engine
        result = self.execution.execute_exit(
            symbol=trade.symbol,
            direction=trade.direction,
            size=trade.position_size,
            reason=reason,
            exit_price=exit_price
        )
        
        # Update database
        from datetime import datetime
        self.db.update_trade_exit(
            trade_id=trade.trade_id,
            exit_price=exit_price,
            exit_time=datetime.utcnow(),
            realized_pnl=pnl,
            exit_fee=0,  # Would calculate from execution result
            slippage_exit=result.get("slippage_bps", 0) / 10000,
            exit_reason=reason
        )
        
        logger.info(
            f"Position closed: {trade.symbol} | Reason: {reason} | "
            f"PnL: ${pnl:.2f} ({pnl_pct*100:+.2f}%) | R: {r_multiple:+.2f}"
        )
        
        return {
            "symbol": trade.symbol,
            "reason": reason,
            "exit_price": exit_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "r_multiple": r_multiple,
            "execution": result,
        }
    
    def get_active_positions(self) -> List[TradeRecord]:
        """Get all currently managed positions."""
        return list(self.active_trades.values())
    
    def get_position_summary(self) -> Dict:
        """Summary of all active positions."""
        total_risk = sum(t.risk_pct for t in self.active_trades.values())
        total_pnl = 0  # Would track unrealized
        
        return {
            "count": len(self.active_trades),
            "symbols": list(self.active_trades.keys()),
            "total_risk_pct": total_risk,
            "positions": [
                {
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry": t.entry_price,
                    "sl": t.stop_loss_price,
                    "tp": t.take_profit_price,
                    "size": t.position_size,
                }
                for t in self.active_trades.values()
            ]
        }
    
    def emergency_close_all(self, reason: str = "kill_switch"):
        """Close all positions immediately."""
        results = []
        for symbol, trade in list(self.active_trades.items()):
            result = self._execute_exit(
                trade,
                {
                    "should_exit": True,
                    "reason": reason,
                    "price": self.market_data.get_latest_price(symbol) or trade.stop_loss_price
                }
            )
            results.append(result)
        
        self.active_trades.clear()
        return results