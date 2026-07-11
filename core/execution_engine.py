"""
Layer 6: Execution Engine.
Handles order placement, slippage protection, retries, partial fills.
"""

import time
from typing import Dict, List, Optional

from core.bitget_client import BitgetClient, BitgetAPIError
from core.market_data import MarketData
from utils.logger import get_logger
from config.settings import EXECUTION, RISK, TRADING_MODE

logger = get_logger(__name__)


def is_live() -> bool:
    """Check if running in live trading mode."""
    return TRADING_MODE == "live"


class ExecutionEngine:
    """
    Layer 6 of the 8-layer architecture.
    Manages all order execution with safety mechanisms.
    """
    
    def __init__(
        self,
        client: Optional[BitgetClient] = None,
        market_data: Optional[MarketData] = None
    ):
        self.client = client or BitgetClient()
        self.market_data = market_data or MarketData()
        self.is_live_mode = is_live()
        
        if not self.is_live_mode:
            logger.warning("ExecutionEngine running in PAPER/BACKTEST mode - no real orders")
    
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: float,
        price: Optional[float] = None,
        leverage: int = 1,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Dict:
        """
        Place a single order (wrapper for trend_pullback.py compatibility).
        Delegates to execute_entry for full handling.
        """
        direction = "long" if side == "buy" else "short"
        return self.execute_entry(
            symbol=symbol,
            direction=direction,
            size=size,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
        )
    
    def close_position(self, symbol: str) -> Dict:
        """
        Close a position by symbol (wrapper for trend_pullback.py compatibility).
        """
        # Get current position info to determine direction
        try:
            positions = self.client.get_positions()
            for pos in positions:
                if pos.get("symbol") == symbol:
                    direction = pos.get("holdSide", "long")
                    size = float(pos.get("total", 0))
                    return self.execute_exit(
                        symbol=symbol,
                        direction=direction,
                        size=size,
                        reason="strategy_exit",
                    )
        except Exception as e:
            logger.warning(f"Could not get position for {symbol}: {e}")
        
        # Fallback: assume long and try to close
        return self.execute_exit(
            symbol=symbol,
            direction="long",
            size=0,
            reason="strategy_exit_fallback",
        )
    
    def execute_entry(
        self,
        symbol: str,
        direction: str,
        size: float,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        leverage: int = 1
    ) -> Dict:
        """
        Execute an entry order with full safety checks.
        """
        side = "buy" if direction == "long" else "sell"
        order_type = "limit" if entry_price and EXECUTION.order_type_preference == "limit" else "market"
        
        # Set leverage first
        try:
            self.client.set_leverage(symbol, leverage)
        except Exception as e:
            logger.warning(f"Failed to set leverage for {symbol}: {e}")
        
        # Determine execution price
        if order_type == "limit" and entry_price:
            current_price = self.market_data.get_latest_price(symbol)
            if current_price:
                if direction == "long" and entry_price > current_price:
                    logger.info(f"{symbol}: Price moved past limit, switching to market")
                    order_type = "market"
                    entry_price = None
                elif direction == "short" and entry_price < current_price:
                    logger.info(f"{symbol}: Price moved past limit, switching to market")
                    order_type = "market"
                    entry_price = None
        
        # Execute with retry logic
        result = self._place_order_with_retry(
            symbol=symbol,
            side=side,
            order_type=order_type,
            size=size,
            price=entry_price
        )
        
        if not result.get("success"):
            return result
        
        # Calculate slippage
        executed_price = float(result.get("price", entry_price or 0))
        expected_price = entry_price or self.market_data.get_latest_price(symbol) or executed_price
        slippage = self._calculate_slippage(expected_price, executed_price, direction)
        
        # Check slippage tolerance
        if slippage > EXECUTION.slippage_max_bps / 10000:
            logger.warning(
                f"{symbol}: Slippage {slippage*10000:.1f} bps exceeds max "
                f"({EXECUTION.slippage_max_bps} bps)"
            )
        
        execution_result = {
            "success": True,
            "order_id": result.get("orderId"),
            "symbol": symbol,
            "direction": direction,
            "side": side,
            "order_type": order_type,
            "requested_size": size,
            "executed_size": float(result.get("size", size)),
            "requested_price": entry_price,
            "executed_price": executed_price,
            "slippage_bps": round(slippage * 10000, 2),
            "leverage": leverage,
            "timestamp": time.time(),
        }
        
        logger.info(
            f"Entry executed: {symbol} {direction} | "
            f"Price: {executed_price} | Slippage: {slippage*10000:.1f} bps | "
            f"Order: {result.get('orderId', 'N/A')}"
        )
        
        return execution_result
    
    def execute_exit(
        self,
        symbol: str,
        direction: str,
        size: float,
        reason: str,
        exit_price: Optional[float] = None
    ) -> Dict:
        """
        Execute an exit order (close position).
        """
        close_side = "sell" if direction == "long" else "buy"
        order_type = "limit" if exit_price and EXECUTION.order_type_preference == "limit" else "market"
        
        result = self._place_order_with_retry(
            symbol=symbol,
            side=close_side,
            order_type=order_type,
            size=size,
            price=exit_price
        )
        
        if not result.get("success"):
            return result
        
        executed_price = float(result.get("price", exit_price or 0))
        expected_price = exit_price or self.market_data.get_latest_price(symbol) or executed_price
        slippage = self._calculate_slippage(expected_price, executed_price, direction)
        
        execution_result = {
            "success": True,
            "order_id": result.get("orderId"),
            "symbol": symbol,
            "reason": reason,
            "executed_price": executed_price,
            "slippage_bps": round(slippage * 10000, 2),
            "timestamp": time.time(),
        }
        
        logger.info(
            f"Exit executed: {symbol} | Reason: {reason} | "
            f"Price: {executed_price} | Slippage: {slippage*10000:.1f} bps"
        )
        
        return execution_result
    
    def _place_order_with_retry(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: float,
        price: Optional[float] = None
    ) -> Dict:
        """
        Place order with retry logic and error handling.
        """
        for attempt in range(1, EXECUTION.retry_attempts + 1):
            try:
                if not self.is_live_mode:
                    # Paper/backtest mode: simulate execution
                    simulated_price = price or self.market_data.get_latest_price(symbol) or 0
                    return {
                        "success": True,
                        "orderId": f"PAPER_{int(time.time()*1000)}",
                        "price": str(simulated_price),
                        "size": str(size),
                        "status": "filled",
                        "simulated": True,
                    }
                
                # Live execution
                response = self.client.place_order(
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    size=size,
                    price=price
                )
                
                # Check for partial fill
                filled_size = float(response.get("filledSize", size))
                if filled_size < size * EXECUTION.partial_fill_threshold:
                    logger.warning(
                        f"{symbol}: Partial fill {filled_size}/{size}. "
                        f"May need to retry remainder."
                    )
                
                return {
                    "success": True,
                    "orderId": response.get("orderId"),
                    "price": response.get("price"),
                    "size": response.get("size"),
                    "status": response.get("status", "unknown"),
                }
                
            except BitgetAPIError as e:
                logger.error(f"Order attempt {attempt} failed: {e}")
                if attempt < EXECUTION.retry_attempts:
                    time.sleep(EXECUTION.retry_delay_seconds * attempt)
                else:
                    return {
                        "success": False,
                        "error": str(e),
                        "attempts": attempt,
                    }
        
        return {"success": False, "error": "Max retries exceeded"}
    
    def _calculate_slippage(
        self,
        expected_price: float,
        executed_price: float,
        direction: str
    ) -> float:
        """
        Calculate slippage as decimal.
        Positive = adverse (worse than expected)
        Negative = favorable (better than expected)
        """
        if expected_price <= 0 or executed_price <= 0:
            return 0.0
        
        if direction == "long":
            return (executed_price - expected_price) / expected_price
        else:
            return (expected_price - executed_price) / expected_price
    
    def cancel_pending_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an unfilled order."""
        try:
            self.client.cancel_order(symbol, order_id)
            logger.info(f"Order cancelled: {symbol} {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False
    
    def get_order_status(self, symbol: str, order_id: str) -> Dict:
        """Check status of an existing order."""
        try:
            return self.client.get_order_detail(symbol, order_id)
        except Exception as e:
            logger.error(f"Failed to get order status {order_id}: {e}")
            return {"status": "unknown", "error": str(e)}
    
    def close_all_positions(self, reason: str = "emergency") -> List[Dict]:
        """
        Emergency close all open positions.
        Used by kill switch.
        """
        results = []
        try:
            positions = self.client.get_positions()
            for pos in positions:
                symbol = pos.get("symbol")
                hold_side = pos.get("holdSide")
                if symbol and hold_side:
                    result = self.client.close_position(symbol, hold_side)
                    results.append({
                        "symbol": symbol,
                        "side": hold_side,
                        "result": result,
                    })
                    logger.info(f"Emergency close: {symbol} {hold_side}")
        except Exception as e:
            logger.error(f"Emergency close failed: {e}")
        
        return results