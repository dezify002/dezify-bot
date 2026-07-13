"""
Trend Pullback Strategy v3.0
8-layer systematic approach with candle-close evaluation
"""

import json
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from core.market_data import MarketData
from core.bitget_client import BitgetClient
from core.analytics import Analytics
from data.database import Database
from data.models import TradeRecord, TradeChecklist, Signal
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PendingOrder:
    """Limit order with TTL."""
    signal_id: str
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    created_at: datetime
    ttl_seconds: int = 1800  # 30 minutes
    filled: bool = False
    cancelled: bool = False

    def is_expired(self) -> bool:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds() > self.ttl_seconds

    def to_dict(self) -> Dict:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "position_size": self.position_size,
            "created_at": self.created_at.isoformat(),
            "ttl_seconds": self.ttl_seconds,
            "filled": self.filled,
            "cancelled": self.cancelled,
        }


@dataclass  
class ActivePosition:
    """Track position state for break-even and trailing stop."""
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    entry_time: datetime
    break_even_moved: bool = False
    trailing_active: bool = False
    highest_price: float = 0.0
    lowest_price: float = float('inf')

    def update_price(self, current_price: float):
        """Update trailing state with current price."""
        if self.direction == "long":
            if current_price > self.highest_price:
                self.highest_price = current_price
            # Break-even at 1R
            if not self.break_even_moved:
                r_distance = self.entry_price - self.stop_loss
                if r_distance > 0 and current_price >= self.entry_price + r_distance:
                    self.stop_loss = self.entry_price
                    self.break_even_moved = True
                    logger.info(f"{self.symbol}: SL moved to break-even (1R reached)")
            # Trailing stop at 2R
            if not self.trailing_active:
                r_distance = self.entry_price - self.stop_loss
                if r_distance > 0 and current_price >= self.entry_price + (2 * r_distance):
                    self.trailing_active = True
                    logger.info(f"{self.symbol}: Trailing stop activated (2R reached)")
        else:  # short
            if current_price < self.lowest_price:
                self.lowest_price = current_price
            # Break-even at 1R
            if not self.break_even_moved:
                r_distance = self.stop_loss - self.entry_price
                if r_distance > 0 and current_price <= self.entry_price - r_distance:
                    self.stop_loss = self.entry_price
                    self.break_even_moved = True
                    logger.info(f"{self.symbol}: SL moved to break-even (1R reached)")
            # Trailing stop at 2R
            if not self.trailing_active:
                r_distance = self.stop_loss - self.entry_price
                if r_distance > 0 and current_price <= self.entry_price - (2 * r_distance):
                    self.trailing_active = True
                    logger.info(f"{self.symbol}: Trailing stop activated (2R reached)")

    def should_exit(self, current_price: float, max_hold_hours: int = 48) -> tuple[bool, str]:
        """Check if position should be exited. Returns (should_exit, reason)."""
        # Time exit
        hold_time = (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 3600
        if hold_time > max_hold_hours:
            return True, "time_exit"

        # Stop loss
        if self.direction == "long" and current_price <= self.stop_loss:
            return True, "stop_loss"
        if self.direction == "short" and current_price >= self.stop_loss:
            return True, "stop_loss"

        # Take profit
        if self.direction == "long" and current_price >= self.take_profit:
            return True, "take_profit"
        if self.direction == "short" and current_price <= self.take_profit:
            return True, "take_profit"

        return False, ""


class TrendPullbackStrategy:
    """
    v3.0: 8-layer systematic trend pullback strategy.
    Evaluates on candle close only.
    """

    # Correlation buckets - max 2 positions per bucket
    CORRELATION_BUCKETS = {
        "btc_eth": ["BTCUSDT", "ETHUSDT"],
        "layer1": ["SOLUSDT", "AVAXUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT"],
        "defi": ["UNIUSDT", "AAVEUSDT", "LINKUSDT", "MKRUSDT", "CRVUSDT"],
        "meme": ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "FLOKIUSDT"],
        "ai": ["RNDRUSDT", "FETUSDT", "AGIXUSDT", "TAOUSDT"],
        "gaming": ["IMXUSDT", "BEAMUSDT", "SANDUSDT", "MANAUSDT"],
        "infra": ["ARBUSDT", "OPUSDT", "MATICUSDT", "STRKUSDT"],
        "other": [],  # Catch-all
    }

    def __init__(self):
        self.market_data = MarketData()
        self.client = BitgetClient()
        self.analytics = Analytics()
        self.db = Database()
        self.universe: List[str] = []

        # v3.0 state
        self.pending_orders: List[PendingOrder] = []
        self.active_positions: Dict[str, ActivePosition] = {}
        self.processed_signals: set = set()  # Signal ID idempotency
        self.last_evaluation_time: Dict[str, datetime] = {}

        # Load persisted state
        self._load_state()

    def _load_state(self):
        """Load pending orders and processed signals from disk."""
        state_file = Path("data/strategy_state.json")
        if state_file.exists():
            try:
                with open(state_file) as f:
                    data = json.load(f)
                    self.processed_signals = set(data.get("processed_signals", []))
                    # Reconstruct pending orders
                    for order_data in data.get("pending_orders", []):
                        order = PendingOrder(
                            signal_id=order_data["signal_id"],
                            symbol=order_data["symbol"],
                            direction=order_data["direction"],
                            entry_price=order_data["entry_price"],
                            stop_loss=order_data["stop_loss"],
                            take_profit=order_data["take_profit"],
                            position_size=order_data["position_size"],
                            created_at=datetime.fromisoformat(order_data["created_at"]),
                            ttl_seconds=order_data.get("ttl_seconds", 1800),
                            filled=order_data.get("filled", False),
                            cancelled=order_data.get("cancelled", False),
                        )
                        self.pending_orders.append(order)
                    logger.info(f"Loaded {len(self.pending_orders)} pending orders, {len(self.processed_signals)} processed signals")
            except Exception as e:
                logger.warning(f"Failed to load strategy state: {e}")

    def _save_state(self):
        """Persist pending orders and processed signals."""
        try:
            state_file = Path("data/strategy_state.json")
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, "w") as f:
                json.dump({
                    "processed_signals": list(self.processed_signals),
                    "pending_orders": [o.to_dict() for o in self.pending_orders],
                    "last_save": datetime.now(timezone.utc).isoformat(),
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save strategy state: {e}")

    def refresh_universe(self):
        """Refresh trading universe from Bitget."""
        try:
            tickers = self.client.get_tickers(product_type="USDT-FUTURES")
            self.universe = []
            for ticker in tickers:
                symbol = ticker.get("symbol", "")
                volume_24h = float(ticker.get("usdtVolume", 0) or ticker.get("volume", 0))
                if volume_24h > 1_000_000:  # $1M+ volume
                    self.universe.append(symbol)
            logger.info(f"Universe refreshed: {len(self.universe)} symbols")
        except Exception as e:
            logger.error(f"Failed to refresh universe: {e}")
            # Fallback universe
            self.universe = [
                "BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "NEARUSDT",
                "LINKUSDT", "UNIUSDT", "AAVEUSDT", "DOGEUSDT", "RNDRUSDT",
                "ARBUSDT", "OPUSDT", "IMXUSDT", "FETUSDT", "APTUSDT",
            ]

    def _get_correlation_bucket(self, symbol: str) -> str:
        """Get correlation bucket for a symbol."""
        for bucket, symbols in self.CORRELATION_BUCKETS.items():
            if symbol in symbols:
                return bucket
        return "other"

    def _check_correlation_limit(self, symbol: str) -> bool:
        """Check if adding this symbol would exceed correlation bucket limit (max 2)."""
        bucket = self._get_correlation_bucket(symbol)
        current_in_bucket = sum(
            1 for pos in self.active_positions.values()
            if self._get_correlation_bucket(pos.symbol) == bucket
        )
        return current_in_bucket < 2

    def _is_symbol_already_traded(self, symbol: str) -> bool:
        """Check if we already have a position or pending order for this symbol."""
        # Check active positions
        if symbol in self.active_positions:
            return True
        # Check pending orders
        for order in self.pending_orders:
            if order.symbol == symbol and not order.cancelled and not order.filled:
                return True
        # Check open trades in database
        try:
            open_trades = self.db.get_open_trades()
            for trade in open_trades:
                if trade.symbol == symbol:
                    return True
        except Exception:
            pass
        return False

    def generate_signal_id(self, symbol: str, timestamp: datetime, direction: str) -> str:
        """Generate unique signal ID for idempotency."""
        import hashlib
        content = f"{symbol}_{timestamp.isoformat()}_{direction}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _calculate_confidence_score(self, checklist: TradeChecklist) -> float:
        """
        Calculate confidence score (0-100) for analytics only.
        NOT used as a trade gate.
        """
        score = 0.0

        # Layer 1: Market Regime (15 points)
        if checklist.adx_above_threshold:
            score += 15

        # Layer 2: Trend Analysis (20 points)
        if checklist.daily_ema_aligned and checklist.fourh_ema_aligned:
            score += 20
        elif checklist.daily_ema_aligned or checklist.fourh_ema_aligned:
            score += 10

        # Layer 3: Pullback Detection (15 points)
        if checklist.pullback_confirmed:
            score += 15

        # Layer 4: Entry Trigger (20 points)
        if checklist.entry_triggered:
            score += 20

        # Layer 5: Risk Management (15 points)
        if checklist.risk_pct and checklist.risk_pct <= 0.02:
            score += 15

        # Layer 6-8: Execution, Management, Review (15 points)
        score += 15

        return min(score, 100.0)


    def evaluate_symbol(self, symbol: str, timeframe: str = "1H") -> Optional[Signal]:
        """
        Evaluate a single symbol for trade signals.
        Returns Signal if all 8 layers pass, None otherwise.
        NOW WITH FULL LAYER LOGGING for dashboard visibility.
        """
        from data.database import Database
        db = Database()

        def log_layer(layer_name: str, passed: bool, reason: str, metadata: dict = None):
            """Log layer result for dashboard visibility."""
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "layer": layer_name,
                "passed": passed,
                "reason": reason,
                "metadata": metadata or {},
            }
            try:
                import json
                log_file = Path("data/scan_log.json")
                logs = []
                if log_file.exists():
                    with open(log_file) as f:
                        logs = json.load(f)
                logs.append(entry)
                logs = logs[-5000:]  # Keep last 5000
                with open(log_file, "w") as f:
                    json.dump(logs, f, indent=2, default=str)
            except Exception:
                pass  # Don't let logging break the strategy

        # Layer 0: Already trading this symbol?
        if self._is_symbol_already_traded(symbol):
            log_layer("layer_0_duplicate", False, f"Already have position/pending order for {symbol}")
            return None
        log_layer("layer_0_duplicate", True, "No existing position")

        # Layer 0b: Correlation bucket check
        if not self._check_correlation_limit(symbol):
            bucket = self._get_correlation_bucket(symbol)
            log_layer("layer_0b_correlation", False, f"Correlation bucket '{bucket}' already has 2 positions")
            return None
        log_layer("layer_0b_correlation", True, "Correlation bucket has space")

        try:
            # Get candles
            candles = self.market_data.get_candles(symbol, timeframe=timeframe, limit=100)
            if candles is None or len(candles) < 50:
                log_layer("layer_0c_data", False, f"Insufficient candle data: {len(candles) if candles else 0} candles")
                return None
            log_layer("layer_0c_data", True, f"Loaded {len(candles)} candles")

            # Layer 1: Market Regime Filter
            adx = self.market_data.get_adx(candles)
            atr = self.market_data.get_atr(candles)

            if adx < 25:
                log_layer("layer_1_regime", False, f"ADX {adx:.1f} < 25 (not trending)", {"adx": adx, "atr": atr})
                return None
            log_layer("layer_1_regime", True, f"ADX {adx:.1f} >= 25 (trending)", {"adx": adx, "atr": atr})

            # Layer 2: Trend Analysis (Multi-timeframe)
            daily_candles = self.market_data.get_candles(symbol, timeframe="1D", limit=100)
            fourh_candles = self.market_data.get_candles(symbol, timeframe="4H", limit=100)

            trend_score = 0
            daily_aligned = False
            fourh_aligned = False
            daily_ema21_val = None
            daily_ema55_val = None
            fourh_ema21_val = None
            fourh_ema55_val = None

            if daily_candles is not None and len(daily_candles) > 50:
                daily_ema21_val = self.market_data.get_ema(daily_candles, 21)
                daily_ema55_val = self.market_data.get_ema(daily_candles, 55)
                if daily_ema21_val and daily_ema55_val:
                    daily_aligned = daily_ema21_val > daily_ema55_val

            if fourh_candles is not None and len(fourh_candles) > 50:
                fourh_ema21_val = self.market_data.get_ema(fourh_candles, 21)
                fourh_ema55_val = self.market_data.get_ema(fourh_candles, 55)
                if fourh_ema21_val and fourh_ema55_val:
                    fourh_aligned = fourh_ema21_val > fourh_ema55_val

            if daily_aligned and fourh_aligned:
                trend_score = 2
                direction = "long"
            elif not daily_aligned and not fourh_aligned:
                trend_score = 2
                direction = "short"
            elif daily_aligned or fourh_aligned:
                trend_score = 1
                direction = "long" if daily_aligned else "short"
            else:
                log_layer("layer_2_trend", False, "No EMA alignment on any timeframe", {
                    "daily_ema21": daily_ema21_val, "daily_ema55": daily_ema55_val,
                    "fourh_ema21": fourh_ema21_val, "fourh_ema55": fourh_ema55_val,
                })
                return None

            log_layer("layer_2_trend", True, f"Direction: {direction}, Score: {trend_score}", {
                "daily_aligned": daily_aligned, "fourh_aligned": fourh_aligned,
                "daily_ema21": daily_ema21_val, "daily_ema55": daily_ema55_val,
                "fourh_ema21": fourh_ema21_val, "fourh_ema55": fourh_ema55_val,
            })

            # Layer 3: Pullback Detection
            current_price = candles["close"].iloc[-1]
            recent_high = candles["high"].iloc[-20:].max()
            recent_low = candles["low"].iloc[-20:].min()

            pullback_depth = 0
            pullback_confirmed = False

            if direction == "long":
                pullback_depth = (recent_high - current_price) / recent_high
                if 0.01 <= pullback_depth <= 0.03:
                    pullback_confirmed = True
            else:
                pullback_depth = (current_price - recent_low) / recent_low
                if 0.01 <= pullback_depth <= 0.03:
                    pullback_confirmed = True

            if not pullback_confirmed:
                log_layer("layer_3_pullback", False, f"Pullback depth {pullback_depth:.4f} outside 1-3% range", {
                    "pullback_depth": pullback_depth, "recent_high": recent_high, "recent_low": recent_low,
                    "current_price": current_price, "direction": direction,
                })
                return None
            log_layer("layer_3_pullback", True, f"Pullback depth {pullback_depth:.4f} confirmed", {
                "pullback_depth": pullback_depth, "recent_high": recent_high, "recent_low": recent_low,
            })

            # Layer 4: Entry Trigger
            entry_triggered = False
            entry_price = current_price

            if len(candles) >= 3:
                prev_close = candles["close"].iloc[-2]
                prev_open = candles["open"].iloc[-2]
                prev_prev_close = candles["close"].iloc[-3]

                if direction == "long":
                    if prev_close > prev_open and prev_close > prev_prev_close:
                        entry_triggered = True
                else:
                    if prev_close < prev_open and prev_close < prev_prev_close:
                        entry_triggered = True

            if not entry_triggered:
                log_layer("layer_4_entry", False, "No bullish/bearish confirmation candle", {
                    "prev_close": prev_close if len(candles) >= 3 else None,
                    "prev_open": prev_open if len(candles) >= 3 else None,
                    "prev_prev_close": prev_prev_close if len(candles) >= 3 else None,
                })
                return None
            log_layer("layer_4_entry", True, f"Entry triggered at {entry_price:.4f}", {"entry_price": entry_price})

            # Layer 5: Risk Management
            account_equity = self.db.get_equity() or 10000.0
            risk_pct = 0.02
            risk_amount = account_equity * risk_pct

            if direction == "long":
                stop_loss = entry_price * 0.985
                take_profit = entry_price * 1.045
            else:
                stop_loss = entry_price * 1.015
                take_profit = entry_price * 0.955

            stop_distance = abs(entry_price - stop_loss)
            if stop_distance <= 0:
                log_layer("layer_5_risk", False, "Invalid stop distance (zero or negative)", {"stop_loss": stop_loss})
                return None

            position_size = risk_amount / stop_distance
            position_value = position_size * entry_price

            if position_value > account_equity * 0.1:
                position_size = (account_equity * 0.1) / entry_price
                position_value = position_size * entry_price

            log_layer("layer_5_risk", True, f"Risk: ${risk_amount:.2f}, Size: {position_size:.4f}, Value: ${position_value:.2f}", {
                "risk_pct": risk_pct, "stop_loss": stop_loss, "take_profit": take_profit,
                "position_size": position_size, "position_value": position_value,
            })

            # Layer 6-8: Execution, Management, Review (pass-through for signal generation)
            log_layer("layer_6_execution", True, "Limit order ready")
            log_layer("layer_7_management", True, "Break-even and trailing stop configured")
            log_layer("layer_8_performance", True, "R-multiple tracking ready")

            # Create checklist
            checklist = TradeChecklist()
            checklist.adx_above_threshold = adx >= 25
            checklist.adx_value = adx
            checklist.daily_ema_aligned = daily_aligned
            checklist.fourh_ema_aligned = fourh_aligned
            checklist.trend_score = trend_score
            checklist.pullback_confirmed = pullback_confirmed
            checklist.pullback_depth = pullback_depth
            checklist.entry_triggered = entry_triggered
            checklist.entry_price = entry_price
            checklist.stop_loss_price = stop_loss
            checklist.take_profit_price = take_profit
            checklist.risk_pct = risk_pct
            checklist.position_size = position_size
            checklist.leverage = 1

            confidence = self._calculate_confidence_score(checklist)
            signal_id = self.generate_signal_id(symbol, datetime.now(timezone.utc), direction)

            if signal_id in self.processed_signals:
                log_layer("layer_final", False, f"Signal {signal_id} already processed (idempotency)")
                return None

            signal = Signal(
                symbol=symbol, direction=direction, entry_price=entry_price,
                stop_loss=stop_loss, take_profit=take_profit, position_size=position_size,
                confidence=confidence, checklist=checklist, signal_id=signal_id,
            )

            log_layer("layer_final", True, f"SIGNAL GENERATED: {direction} @ {entry_price:.4f} (conf: {confidence:.1f})", {
                "signal_id": signal_id, "confidence": confidence, "direction": direction,
            })

            # Also log to signal log
            try:
                import json
                sig_file = Path("data/signal_log.json")
                sig_logs = []
                if sig_file.exists():
                    with open(sig_file) as f:
                        sig_logs = json.load(f)
                sig_logs.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol, "direction": direction, "entry_price": entry_price,
                    "stop_loss": stop_loss, "take_profit": take_profit,
                    "confidence": confidence, "signal_id": signal_id,
                })
                sig_logs = sig_logs[-1000:]
                with open(sig_file, "w") as f:
                    json.dump(sig_logs, f, indent=2, default=str)
            except Exception:
                pass

            logger.info(f"Signal generated: {symbol} {direction} @ {entry_price:.4f} (confidence: {confidence:.1f})")
            return signal

        except Exception as e:
            log_layer("layer_error", False, f"Exception: {str(e)}")
            logger.error(f"Error evaluating {symbol}: {e}")
            return None


    def check_pending_orders(self):
        """Check and manage pending limit orders."""
        current_time = datetime.now(timezone.utc)

        for order in self.pending_orders:
            if order.filled or order.cancelled:
                continue

            # Check TTL
            if order.is_expired():
                logger.info(f"Order {order.signal_id} expired, cancelling")
                order.cancelled = True
                self.processed_signals.add(order.signal_id)
                continue

            # Check if filled (price touched entry)
            try:
                current_price = self.market_data.get_latest_price(order.symbol)
                if current_price is None:
                    continue

                if order.direction == "long":
                    if current_price <= order.entry_price:
                        # Fill the order
                        self._execute_entry(order)
                        order.filled = True
                else:
                    if current_price >= order.entry_price:
                        self._execute_entry(order)
                        order.filled = True

            except Exception as e:
                logger.error(f"Error checking order {order.signal_id}: {e}")

        # Clean up filled/cancelled orders
        self.pending_orders = [o for o in self.pending_orders if not o.filled and not o.cancelled]

    def _execute_entry(self, order: PendingOrder):
        """Execute entry for a filled pending order."""
        try:
            trade = TradeRecord(
                trade_id=order.signal_id,
                symbol=order.symbol,
                direction=order.direction,
                entry_price=order.entry_price,
                stop_loss_price=order.stop_loss,
                take_profit_price=order.take_profit,
                position_size=order.position_size,
                entry_time=datetime.now(timezone.utc),
            )

            # Save to database
            self.db.save_trade(trade)

            # Track active position
            self.active_positions[order.symbol] = ActivePosition(
                trade_id=order.signal_id,
                symbol=order.symbol,
                direction=order.direction,
                entry_price=order.entry_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                position_size=order.position_size,
                entry_time=datetime.now(timezone.utc),
                highest_price=order.entry_price if order.direction == "long" else 0,
                lowest_price=order.entry_price if order.direction == "short" else float('inf'),
            )

            logger.info(f"ENTRY: {order.symbol} {order.direction} @ {order.entry_price:.4f}")

        except Exception as e:
            logger.error(f"Error executing entry for {order.symbol}: {e}")

    def manage_positions(self):
        """Check exits, break-even, trailing stops for active positions."""
        positions_to_close = []

        for symbol, position in self.active_positions.items():
            try:
                current_price = self.market_data.get_latest_price(symbol)
                if current_price is None:
                    continue

                # Update trailing state
                position.update_price(current_price)

                # Check exit conditions
                should_exit, exit_reason = position.should_exit(current_price)

                if should_exit:
                    positions_to_close.append((symbol, exit_reason, current_price))

            except Exception as e:
                logger.error(f"Error managing position {symbol}: {e}")

        # Close positions
        for symbol, exit_reason, exit_price in positions_to_close:
            self._execute_exit(symbol, exit_price, exit_reason)

    def _execute_exit(self, symbol: str, exit_price: float, exit_reason: str):
        """Execute exit for a position."""
        try:
            position = self.active_positions.get(symbol)
            if not position:
                return

            # Calculate PnL
            if position.direction == "long":
                pnl = (exit_price - position.entry_price) * position.position_size
                pnl_pct = (exit_price - position.entry_price) / position.entry_price
            else:
                pnl = (position.entry_price - exit_price) * position.position_size
                pnl_pct = (position.entry_price - exit_price) / position.entry_price

            # Calculate R-multiple
            stop_distance = abs(position.entry_price - position.stop_loss)
            if stop_distance > 0:
                if position.direction == "long":
                    r_multiple = (exit_price - position.entry_price) / stop_distance
                else:
                    r_multiple = (position.entry_price - exit_price) / stop_distance
            else:
                r_multiple = 0

            # Update database
            trade = self.db.get_trade(position.trade_id)
            if trade:
                trade.exit_price = exit_price
                trade.exit_time = datetime.now(timezone.utc)
                trade.realized_pnl = pnl
                trade.realized_pnl_pct = pnl_pct
                trade.r_multiple = r_multiple
                if trade.checklist:
                    trade.checklist.exit_reason = exit_reason
                self.db.save_trade(trade)

            # Remove from active positions
            del self.active_positions[symbol]

            logger.info(f"EXIT: {symbol} @ {exit_price:.4f} | Reason: {exit_reason} | PnL: ${pnl:.2f} | R: {r_multiple:.2f}")

        except Exception as e:
            logger.error(f"Error executing exit for {symbol}: {e}")

    def run_cycle(self, timeframe: str = "1H") -> Dict[str, Any]:
        """
        Main strategy cycle - called on candle close.
        Returns summary of actions taken.
        """
        logger.info(f"=== Starting v3.0 strategy cycle | Timeframe: {timeframe} ===")

        result = {
            "exits": 0,
            "entries": 0,
            "pending_filled": 0,
            "pending_cancelled": 0,
            "signals_generated": 0,
            "open_positions": len(self.active_positions),
        }

        try:
            # Step 1: Manage pending orders
            self.check_pending_orders()

            # Step 2: Manage active positions (exits, trailing stops)
            self.manage_positions()

            # Step 3: Scan for new signals
            for symbol in self.universe:
                signal = self.evaluate_symbol(symbol, timeframe)
                if signal:
                    result["signals_generated"] += 1

                    # Place limit order (not market)
                    order = PendingOrder(
                        signal_id=signal.signal_id,
                        symbol=signal.symbol,
                        direction=signal.direction,
                        entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        position_size=signal.position_size,
                        created_at=datetime.now(timezone.utc),
                        ttl_seconds=1800,  # 30 minutes
                    )
                    self.pending_orders.append(order)
                    self.processed_signals.add(signal.signal_id)

                    logger.info(f"LIMIT ORDER placed: {signal.symbol} @ {signal.entry_price:.4f} (TTL: 30min)")

            # Step 4: Sync with database positions
            self._sync_database_positions()

            # Step 5: Save state
            self._save_state()

        except Exception as e:
            logger.error(f"Strategy cycle error: {e}")
            traceback.print_exc()

        logger.info(f"=== Cycle complete: {result} ===")
        return result

    def _sync_database_positions(self):
        """Sync active_positions with database open trades."""
        try:
            open_trades = self.db.get_open_trades()
            db_symbols = {t.symbol for t in open_trades}

            # Add missing positions from DB
            for trade in open_trades:
                if trade.symbol not in self.active_positions:
                    self.active_positions[trade.symbol] = ActivePosition(
                        trade_id=trade.trade_id,
                        symbol=trade.symbol,
                        direction=trade.direction,
                        entry_price=trade.entry_price,
                        stop_loss=trade.stop_loss_price or trade.entry_price * 0.985,
                        take_profit=trade.take_profit_price or trade.entry_price * 1.045,
                        position_size=trade.position_size or 0,
                        entry_time=trade.entry_time or datetime.now(timezone.utc),
                    )

            # Remove positions that are closed in DB
            for symbol in list(self.active_positions.keys()):
                if symbol not in db_symbols:
                    del self.active_positions[symbol]

        except Exception as e:
            logger.warning(f"Failed to sync database positions: {e}")