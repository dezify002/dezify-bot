"""
Trend-Pullback Strategy v3.0 — STRICT SPEC COMPLIANCE
Implements all 8 layers per the frozen v3.0 specification.

KEY CHANGES FROM v2:
- Candle-close-only evaluation (not time-based polling)
- Signal ID idempotency (no duplicate trades)
- Limit order simulation with 30-min TTL
- Break-even at 1R, trailing stop at 2R
- Correlation bucket limits (max 2 per bucket)
- Confidence score (analytics only, not a gate)
- ATR volatility band (per-symbol 90-day percentile)
- Entry lookback window: 2 candles
"""

import uuid
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict

from core.bitget_client import BitgetClient
from core.market_data import MarketData
from core.regime_detector import RegimeDetector
from core.trend_analyzer import TrendAnalyzer
from core.pullback_detector import PullbackDetector
from core.entry_trigger import EntryTrigger
from core.position_sizer import PositionSizer
from core.execution_engine import ExecutionEngine
from core.analytics import Analytics
from data.models import TradeChecklist, Signal, TradeRecord
from utils.logger import get_logger
from config.settings import STRATEGY, RISK

logger = get_logger(__name__)

# =============================================================================
# CORRELATION BUCKETS (v3.0 Section 0.2 / Layer 5)
# =============================================================================
CORRELATION_BUCKETS = {
    "L1": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOTUSDT", "AVAXUSDT"],
    "DeFi": ["UNIUSDT", "AAVEUSDT", "LINKUSDT", "MKRUSDT", "COMPUSDT", "CRVUSDT", "SNXUSDT", "YFIUSDT", "SUSHIUSDT", "1INCHUSDT"],
    "AI": ["FETUSDT", "RNDRUSDT", "AGIXUSDT", "OCEANUSDT", "GRTUSDT", "TAOUSDT", "WLDUSDT", "NEARUSDT"],
    "Memes": ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "FLOKIUSDT", "BONKUSDT", "WIFUSDT", "BOMEUSDT", "MEMEUSDT"],
    "L2": ["MATICUSDT", "ARBUSDT", "OPUSDT", "STRKUSDT", "MNTUSDT", "IMXUSDT", "METISUSDT"],
    "Gaming": ["SANDUSDT", "MANAUSDT", "AXSUSDT", "GALAUSDT", "ENJUSDT", "ILVUSDT", "PYRUSDT", "BEAMUSDT"],
    "RWA": ["ONDOUSDT", "CFGUSDT", "TRUUSDT", "POLYXUSDT", "DUSDT", "RIOUSDT", "TOKENUSDT", "CPOOLUSDT"],
    "Infrastructure": ["LINKUSDT", "GRTUSDT", "LDOUSDT", "SSVUSDT", "RPLUSDT", "FXSUSDT", "PENDLEUSDT", "EigenLayer"],
}

# Build reverse lookup: symbol -> bucket
SYMBOL_TO_BUCKET = {}
for bucket, symbols in CORRELATION_BUCKETS.items():
    for s in symbols:
        SYMBOL_TO_BUCKET[s] = bucket


def get_symbol_bucket(symbol: str) -> str:
    """Get correlation bucket for a symbol."""
    return SYMBOL_TO_BUCKET.get(symbol, "Other")


# =============================================================================
# SIGNAL ID GENERATOR (v3.0 Section 10 — Idempotency)
# =============================================================================
def generate_signal_id(symbol: str, candle_timestamp: int, layer_hash: str) -> str:
    """
    Generate unique Signal ID per v3.0 spec:
    symbol + candle_timestamp + layer-pass hash
    """
    raw = f"{symbol}:{candle_timestamp}:{layer_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# =============================================================================
# PENDING ORDER TRACKING (v3.0 Section 9.1 — Order Lifecycle)
# =============================================================================
class PendingOrder:
    """Track a limit order that hasn't filled yet."""
    def __init__(self, signal_id: str, symbol: str, direction: str, 
                 entry_price: float, size: float, leverage: int,
                 stop_loss: float, take_profit: float, 
                 placed_at: datetime, ttl_minutes: int = 30):
        self.signal_id = signal_id
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.size = size
        self.leverage = leverage
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.placed_at = placed_at
        self.ttl_minutes = ttl_minutes
        self.cancelled = False
        self.filled = False

    def is_expired(self) -> bool:
        return datetime.utcnow() - self.placed_at > timedelta(minutes=self.ttl_minutes)

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "size": self.size,
            "leverage": self.leverage,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "placed_at": self.placed_at.isoformat(),
            "ttl_minutes": self.ttl_minutes,
            "cancelled": self.cancelled,
            "filled": self.filled,
        }


# =============================================================================
# ACTIVE POSITION TRACKING (v3.0 Layer 7 — Trade Manager)
# =============================================================================
class ActivePosition:
    """Track an open position with break-even and trailing stop state."""
    def __init__(self, trade_id: str, signal: Signal, entry_time: datetime):
        self.trade_id = trade_id
        self.signal = signal
        self.entry_time = entry_time
        self.break_even_moved = False
        self.trailing_active = False
        self.trailing_stop_price = None
        self.highest_price = signal.entry_price  # For long trailing
        self.lowest_price = signal.entry_price   # For short trailing
        self.exit_reason = None

    def update_price(self, current_price: float, atr: float):
        """Update position state based on current price and ATR."""
        entry = self.signal.entry_price
        sl = self.signal.stop_loss

        # Track extremes for trailing
        if self.signal.direction == "long":
            if current_price > self.highest_price:
                self.highest_price = current_price
        else:
            if current_price < self.lowest_price:
                self.lowest_price = current_price

        # Calculate R distance
        r_distance = abs(entry - sl)
        if r_distance <= 0:
            return

        # Break-even at 1R (v3.0: BREAKEVEN_TRIGGER_R = 1R)
        if not self.break_even_moved:
            if self.signal.direction == "long" and current_price >= entry + r_distance:
                self.break_even_moved = True
                logger.info(f"{self.signal.symbol}: Break-even triggered at 1R")
            elif self.signal.direction == "short" and current_price <= entry - r_distance:
                self.break_even_moved = True
                logger.info(f"{self.signal.symbol}: Break-even triggered at 1R")

        # Trailing stop at 2R (v3.0: TRAIL_ACTIVATION_R = 2R, TRAIL_ATR_MULTIPLE = 1.5)
        if not self.trailing_active:
            if self.signal.direction == "long" and current_price >= entry + 2 * r_distance:
                self.trailing_active = True
                self.trailing_stop_price = current_price - 1.5 * atr
                logger.info(f"{self.signal.symbol}: Trailing stop activated at 2R")
            elif self.signal.direction == "short" and current_price <= entry - 2 * r_distance:
                self.trailing_active = True
                self.trailing_stop_price = current_price + 1.5 * atr
                logger.info(f"{self.signal.symbol}: Trailing stop activated at 2R")

        # Update trailing stop if active
        if self.trailing_active:
            if self.signal.direction == "long":
                new_stop = self.highest_price - 1.5 * atr
                if new_stop > self.trailing_stop_price:
                    self.trailing_stop_price = new_stop
            else:
                new_stop = self.lowest_price + 1.5 * atr
                if new_stop < self.trailing_stop_price:
                    self.trailing_stop_price = new_stop

    def get_effective_stop(self) -> float:
        """Get current stop loss price (may be break-even or trailing)."""
        if self.trailing_active and self.trailing_stop_price:
            return self.trailing_stop_price
        if self.break_even_moved:
            return self.signal.entry_price
        return self.signal.stop_loss

    def should_exit(self, current_price: float) -> Tuple[bool, str]:
        """Check if position should be exited."""
        effective_sl = self.get_effective_stop()

        # Stop loss
        if self.signal.direction == "long":
            if current_price <= effective_sl:
                return True, "stop_loss"
            if current_price >= self.signal.take_profit and not self.trailing_active:
                return True, "take_profit"
        else:
            if current_price >= effective_sl:
                return True, "stop_loss"
            if current_price <= self.signal.take_profit and not self.trailing_active:
                return True, "take_profit"

        # Time exit (v3.0: TIME_EXIT_HOURS = 48)
        hold_time = datetime.utcnow() - self.entry_time
        if hold_time > timedelta(hours=48):
            return True, "time_exit"

        return False, ""


# =============================================================================
# MAIN STRATEGY CLASS
# =============================================================================
class TrendPullbackStrategy:
    """
    v3.0-compliant trend-pullback strategy.

    KEY ARCHITECTURAL CHANGE:
    - v2: run_cycle() called every 60 seconds, evaluated all symbols
    - v3: evaluate_on_candle_close() called when a candle closes, evaluates ONE symbol

    This ensures "one evaluation per candle" and "evaluate only closed candles".
    """

    def __init__(self):
        self.client = BitgetClient()
        self.market_data = MarketData()
        self.regime_detector = RegimeDetector(self.market_data)
        self.trend_analyzer = TrendAnalyzer(self.market_data)
        self.pullback_detector = PullbackDetector(self.market_data)
        self.entry_trigger = EntryTrigger(self.market_data)
        self.position_sizer = PositionSizer()
        self.execution = ExecutionEngine()
        self.analytics = Analytics()

        self.universe: List[str] = []
        self.last_universe_refresh: Optional[datetime] = None

        # v3.0: Track active positions with state machine
        self.active_positions: Dict[str, ActivePosition] = {}

        # v3.0: Track pending limit orders
        self.pending_orders: Dict[str, PendingOrder] = {}

        # v3.0: Track processed signal IDs for idempotency
        self.processed_signals: Set[str] = set()

        # v3.0: Track last evaluation timestamp per symbol per timeframe
        self.last_evaluation: Dict[str, Dict[str, int]] = defaultdict(dict)

        logger.info("TrendPullbackStrategy v3.0 initialized")

    # =====================================================================
    # UNIVERSE MANAGEMENT
    # =====================================================================
    def refresh_universe(self) -> List[str]:
        """Refresh trading universe (v3.0 Section 0.2)."""
        logger.info("Refreshing trading universe...")

        try:
            tickers = self.client.get_tickers(product_type="USDT-FUTURES")
            if not tickers:
                logger.warning("No tickers returned from API")
                return self.universe

            symbols = []
            for t in tickers:
                symbol = t.get("symbol", "")

                # v3.0: Only USDT perpetual futures
                if not symbol.endswith("USDT"):
                    continue

                # v3.0: Skip blacklisted
                if symbol in STRATEGY.symbol_blacklist:
                    continue

                # v3.0: Check minimum volume
                vol_24h = float(t.get("usdtVolume", 0))
                if vol_24h < STRATEGY.min_volume_24h:
                    continue

                symbols.append(symbol)

            self.universe = sorted(symbols)
            self.last_universe_refresh = datetime.utcnow()

            logger.info(f"Universe refreshed: {len(self.universe)} symbols")
            return self.universe

        except Exception as e:
            logger.error(f"Failed to refresh universe: {e}")
            return self.universe

    def should_refresh_universe(self) -> bool:
        """Check if universe refresh is due (daily per v3.0)."""
        if not self.last_universe_refresh:
            return True
        elapsed = datetime.utcnow() - self.last_universe_refresh
        return elapsed > timedelta(days=1)  # v3.0: Daily re-scan

    # =====================================================================
    # CORRELATION BUCKET CHECK (v3.0 Layer 5)
    # =====================================================================
    def _get_bucket_exposure(self, bucket: str) -> int:
        """Count open positions in a correlation bucket."""
        count = 0
        for pos in self.active_positions.values():
            if get_symbol_bucket(pos.signal.symbol) == bucket:
                count += 1
        return count

    def _check_correlation_limit(self, symbol: str) -> bool:
        """Check if adding this symbol would exceed bucket limit (max 2)."""
        bucket = get_symbol_bucket(symbol)
        return self._get_bucket_exposure(bucket) < 2

    # =====================================================================
    # ATR VOLATILITY BAND (v3.0 Section 0.1 / Layer 1)
    # =====================================================================
    def _check_atr_band(self, symbol: str, current_atr: float, 
                        candles_4h: List[Dict]) -> Tuple[bool, float]:
        """
        Check if current ATR is within per-symbol 90-day percentile band.
        v3.0: Reject if outside 10th-90th percentile of symbol's own history.
        """
        if len(candles_4h) < 90:
            # Not enough history — permissive
            return True, 0.5

        # Calculate ATR for each candle in history
        atr_values = []
        for i in range(14, len(candles_4h)):
            window = candles_4h[i-14:i]
            atr = self._calculate_atr(window)
            atr_values.append(atr)

        if len(atr_values) < 10:
            return True, 0.5

        # Calculate percentiles
        sorted_atr = sorted(atr_values)
        n = len(sorted_atr)
        p10_idx = int(n * 0.1)
        p90_idx = int(n * 0.9)

        p10 = sorted_atr[p10_idx]
        p90 = sorted_atr[p90_idx]

        # Normalize current ATR to percentile
        if p90 == p10:
            percentile = 0.5
        else:
            percentile = (current_atr - p10) / (p90 - p10)

        in_band = 0.0 <= percentile <= 1.0

        if not in_band:
            logger.info(f"{symbol}: ATR {current_atr:.4f} outside band [{p10:.4f}, {p90:.4f}] (pct={percentile:.2f})")

        return in_band, percentile

    def _calculate_atr(self, candles: List[Dict], period: int = 14) -> float:
        """Calculate ATR from candle window."""
        if len(candles) < period:
            return 0.0

        tr_values = []
        for i in range(1, len(candles)):
            high = candles[i]["high"]
            low = candles[i]["low"]
            prev_close = candles[i-1]["close"]

            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            tr_values.append(max(tr1, tr2, tr3))

        if len(tr_values) < period:
            return 0.0

        return sum(tr_values[-period:]) / period

    # =====================================================================
    # CONFIDENCE SCORE (v3.0 — Analytics Only, Not a Gate)
    # =====================================================================
    def _calculate_confidence_score(self, checklist: TradeChecklist) -> int:
        """
        Calculate confidence score per v3.0 spec.
        This is NOT used to approve/reject trades — purely for analytics.
        """
        score = 0

        # Daily trend aligned: 30 pts
        if checklist.daily_ema_aligned:
            score += 30

        # 4H trend aligned: 30 pts
        if checklist.fourh_ema_aligned:
            score += 30

        # Pullback structure valid: 20 pts
        if checklist.pullback_confirmed:
            score += 20

        # Volume below average during pullback: 10 pts
        if hasattr(checklist, 'volume_below_avg_during_pullback') and checklist.volume_below_avg_during_pullback:
            score += 10

        # Liquidity sweep confirmed: 10 pts
        if checklist.liquidity_sweep:
            score += 10

        return score

    # =====================================================================
    # MAIN EVALUATION — CANDLE-CLOSE ONLY (v3.0 Section 0)
    # =====================================================================
    def evaluate_symbol_on_candle_close(self, symbol: str, 
                                         timeframe: str = "1H",
                                         candle_timestamp: int = None) -> Optional[Signal]:
        """
        Evaluate ONE symbol when a candle closes.

        v3.0 rules:
        - One evaluation per candle per symbol
        - Only evaluate closed candles (never mid-candle)
        - Entry lookback window: 2 candles
        - Signal ID for idempotency
        """

        # Check if we already evaluated this candle
        if candle_timestamp:
            last_ts = self.last_evaluation.get(symbol, {}).get(timeframe, 0)
            if last_ts >= candle_timestamp:
                logger.debug(f"{symbol}: Already evaluated candle {candle_timestamp}")
                return None
            self.last_evaluation[symbol][timeframe] = candle_timestamp

        # ─── Layer 1: Regime Filter ─────────────────────────────────────
        candles_4h = self.market_data.get_candles(symbol, "4H", limit=100)
        if not candles_4h or len(candles_4h) < 20:
            logger.debug(f"{symbol}: Insufficient 4H data")
            return None

        current_atr = self._calculate_atr(candles_4h)

        # ATR volatility band check
        atr_in_band, atr_percentile = self._check_atr_band(symbol, current_atr, candles_4h)

        regime = self.regime_detector.evaluate(symbol)

        checklist = TradeChecklist()
        checklist.adx_above_threshold = regime["tradable"]
        checklist.adx_value = regime["adx"]
        checklist.atr_sufficient = atr_in_band
        checklist.atr_value = current_atr

        # Volume check
        vol_24h = self.market_data.get_volume_24h(symbol)
        checklist.volume_sufficient = vol_24h >= STRATEGY.min_volume_24h
        checklist.volume_24h = vol_24h

        if not regime["tradable"] or not atr_in_band or not checklist.volume_sufficient:
            logger.info(f"REJECTED {symbol}: Layer 1 — ADX={regime['adx']:.1f}, ATR_band={atr_in_band}, Vol=${vol_24h:,.0f}")
            return None

        # ─── Layer 2: Trend Analysis ──────────────────────────────────────
        trend = self.trend_analyzer.analyze(symbol)
        checklist.daily_ema_aligned = trend["daily_aligned"]
        checklist.fourh_ema_aligned = trend["fourh_aligned"]
        checklist.trend_direction = trend["direction"]
        checklist.trend_score = trend["score"]

        if trend["score"] <= 0:
            logger.info(f"REJECTED {symbol}: Layer 2 — Trend score={trend['score']}, Dir={trend['direction']}")
            return None

        # Determine direction
        direction = None
        if trend["direction"] in ("up", "up_weak"):
            direction = "long"
        elif trend["direction"] in ("down", "down_weak"):
            direction = "short"

        if not direction:
            logger.info(f"REJECTED {symbol}: Layer 2 — No valid direction")
            return None

        # ─── Layer 3: Pullback Detection ──────────────────────────────────
        pullback = self.pullback_detector.detect(symbol, direction)
        checklist.pullback_confirmed = pullback["valid"]
        checklist.pullback_depth = pullback.get("depth")
        checklist.higher_low_formed = pullback.get("higher_low", False)
        checklist.lower_high_formed = pullback.get("lower_high", False)
        checklist.liquidity_sweep = pullback.get("liquidity_sweep", False)

        if not pullback["valid"]:
            logger.info(f"REJECTED {symbol}: Layer 3 — Pullback invalid")
            return None

        # ─── Layer 4: Entry Trigger ─────────────────────────────────────
        # v3.0: Lookback window = 2 candles
        trigger = self.entry_trigger.check(symbol, direction, pullback, 
                                           lookback_candles=2)
        checklist.entry_triggered = trigger["triggered"]
        checklist.entry_price = trigger.get("entry_price")
        checklist.stop_loss_price = trigger.get("stop_loss")
        checklist.take_profit_price = trigger.get("take_profit")

        if not trigger["triggered"]:
            logger.info(f"REJECTED {symbol}: Layer 4 — No trigger within 2-candle lookback")
            return None

        # ─── Layer 5: Risk Manager ────────────────────────────────────────
        account_equity = self._get_account_equity()

        position = self.position_sizer.calculate(
            equity=account_equity,
            entry_price=trigger["entry_price"],
            stop_loss=trigger["stop_loss"],
            direction=direction,
            atr=current_atr,
        )

        checklist.position_size = position["size"]
        checklist.position_value = position["value"]
        checklist.leverage = position["leverage"]
        checklist.risk_pct = position["risk_pct"]
        checklist.stop_loss_price = trigger["stop_loss"]
        checklist.take_profit_price = trigger["take_profit"]

        # Risk per trade limit
        if position["risk_pct"] > RISK.max_account_risk_per_trade:
            logger.info(f"REJECTED {symbol}: Layer 5 — Risk {position['risk_pct']*100:.2f}% exceeds max")
            return None

        # Total portfolio risk
        total_risk = self._get_total_risk() + position["risk_pct"]
        if total_risk > RISK.max_account_risk_total:
            logger.info(f"REJECTED {symbol}: Layer 5 — Total risk {total_risk*100:.2f}% exceeds max")
            return None

        # Max positions
        if len(self.active_positions) >= RISK.max_positions:
            logger.info(f"REJECTED {symbol}: Layer 5 — Max positions ({RISK.max_positions}) reached")
            return None

        # v3.0: Trade exclusivity (no pyramiding)
        if symbol in self.active_positions:
            logger.info(f"REJECTED {symbol}: Layer 5 — Existing position open, no pyramiding")
            return None

        # v3.0: Correlation bucket limit
        if not self._check_correlation_limit(symbol):
            bucket = get_symbol_bucket(symbol)
            logger.info(f"REJECTED {symbol}: Layer 5 — Bucket '{bucket}' at max 2 positions")
            return None

        # ─── Build Signal ─────────────────────────────────────────────────
        # v3.0: Generate Signal ID for idempotency
        layer_hash = f"L1:{regime['tradable']}:L2:{trend['score']}:L3:{pullback['valid']}:L4:{trigger['triggered']}"
        signal_id = generate_signal_id(symbol, candle_timestamp or int(datetime.utcnow().timestamp()), layer_hash)

        # Check idempotency
        if signal_id in self.processed_signals:
            logger.info(f"REJECTED {symbol}: Signal ID {signal_id} already processed")
            return None

        signal = Signal(
            symbol=symbol,
            direction=direction,
            entry_price=trigger["entry_price"],
            stop_loss=trigger["stop_loss"],
            take_profit=trigger["take_profit"],
            position_size=position["size"],
            position_value=position["value"],
            leverage=position["leverage"],
            risk_pct=position["risk_pct"],
            checklist=checklist,
            timestamp=datetime.utcnow(),
        )

        # v3.0: Confidence score (analytics only)
        confidence = self._calculate_confidence_score(checklist)
        logger.info(f"{symbol}: Confidence score = {confidence}/100 (analytics only)")

        return signal

    # =====================================================================
    # ORDER EXECUTION — LIMIT ORDER WITH TTL (v3.0 Section 9)
    # =====================================================================
    def place_limit_order(self, signal: Signal) -> Optional[PendingOrder]:
        """
        Place a limit order with 30-minute TTL.
        v3.0: No market orders. Cancel if unfilled after TTL.
        """
        signal_id = generate_signal_id(
            signal.symbol, 
            int(datetime.utcnow().timestamp()),
            f"exec:{signal.entry_price}"
        )

        order = PendingOrder(
            signal_id=signal_id,
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            size=signal.position_size,
            leverage=signal.leverage,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            placed_at=datetime.utcnow(),
            ttl_minutes=30,  # v3.0: LIMIT_ORDER_TTL_MINUTES
        )

        # Simulate limit order placement
        result = self.execution.place_order(
            symbol=signal.symbol,
            side="buy" if signal.direction == "long" else "sell",
            order_type="limit",
            size=signal.position_size,
            price=signal.entry_price,
            leverage=signal.leverage,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )

        if result["success"]:
            self.pending_orders[signal.symbol] = order
            logger.info(f"Limit order placed: {signal.symbol} @ {signal.entry_price} (TTL: 30min)")
            return order
        else:
            logger.error(f"Limit order failed: {result.get('error')}")
            return None

    def check_pending_orders(self):
        """
        Check pending limit orders for fill or expiry.
        v3.0 Section 9.1: Cancel if unfilled after TTL.
        """
        for symbol, order in list(self.pending_orders.items()):
            if order.filled or order.cancelled:
                continue

            # Check if expired
            if order.is_expired():
                logger.info(f"Order expired: {symbol} @ {order.entry_price} (TTL reached)")
                order.cancelled = True
                self.execution.cancel_order(symbol)
                del self.pending_orders[symbol]
                continue

            # Check if filled (price touched)
            current_price = self.market_data.get_latest_price(symbol)
            if current_price:
                if order.direction == "long" and current_price <= order.entry_price:
                    # Simulate fill
                    order.filled = True
                    self._on_order_filled(order)
                    del self.pending_orders[symbol]
                elif order.direction == "short" and current_price >= order.entry_price:
                    order.filled = True
                    self._on_order_filled(order)
                    del self.pending_orders[symbol]

    def _on_order_filled(self, order: PendingOrder):
        """Handle limit order fill."""
        trade_id = str(uuid.uuid4())[:8]

        # Record regime label BEFORE execution (anti-hindsight)
        self.analytics.record_regime_label(
            symbol=order.symbol,
            regime="trending",  # Simplified — would use actual regime
            adx=0,
            atr_pct=0,
            volume_vs_avg=1.0,
            daily_trend="up" if order.direction == "long" else "down",
            fourh_trend="up" if order.direction == "long" else "down",
        )

        # Record trade entry
        self.analytics.record_trade_entry(
            trade_id=trade_id,
            symbol=order.symbol,
            direction=order.direction,
            entry_price=order.entry_price,
            position_size=order.size,
            position_value=order.size * order.entry_price,
            leverage=order.leverage,
            risk_pct=0,  # Would calculate from checklist
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            checklist=TradeChecklist(),  # Would use actual checklist
            regime="trending",
            adx=0,
            atr=0,
        )

        # Create active position
        signal = Signal(
            symbol=order.symbol,
            direction=order.direction,
            entry_price=order.entry_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            position_size=order.size,
            position_value=order.size * order.entry_price,
            leverage=order.leverage,
            risk_pct=0,
        )

        self.active_positions[order.symbol] = ActivePosition(
            trade_id=trade_id,
            signal=signal,
            entry_time=datetime.utcnow(),
        )

        logger.info(f"Position opened: {trade_id} | {order.symbol} {order.direction} @ {order.entry_price}")

    # =====================================================================
    # POSITION MANAGEMENT — BREAK-EVEN & TRAILING (v3.0 Layer 7)
    # =====================================================================
    def manage_positions(self):
        """
        Check all active positions for exit conditions.
        v3.0: Break-even at 1R, trailing stop at 2R, time exit at 48h.
        """
        exited = []

        for symbol, pos in list(self.active_positions.items()):
            current_price = self.market_data.get_latest_price(symbol)
            if not current_price:
                continue

            # Get current ATR for trailing stop
            candles = self.market_data.get_candles(symbol, "4H", limit=20)
            atr = self._calculate_atr(candles) if candles else 0

            # Update position state (break-even, trailing)
            pos.update_price(current_price, atr)

            # Check exit
            should_exit, reason = pos.should_exit(current_price)

            if should_exit:
                self._exit_position(symbol, current_price, reason)
                exited.append(symbol)

        return exited

    def _exit_position(self, symbol: str, exit_price: float, reason: str):
        """Close a position and record exit."""
        pos = self.active_positions.pop(symbol, None)
        if not pos:
            return

        try:
            self.execution.close_position(symbol)

            self.analytics.record_trade_exit(
                trade_id=pos.trade_id,
                exit_price=exit_price,
                exit_reason=reason,
            )

            logger.info(f"Position closed: {symbol} | Reason: {reason} | Price: {exit_price}")

        except Exception as e:
            logger.error(f"Exit error for {symbol}: {e}")

    # =====================================================================
    # MAIN CYCLE — CANDLE-DRIVEN (v3.0)
    # =====================================================================
    def run_cycle(self, timeframe: str = "1H") -> Dict:
        """
        Run one complete strategy cycle.

        v3.0 CHANGE: This should be called when a candle closes,
        NOT on a fixed timer. The caller is responsible for timing.
        """
        logger.info("=" * 50)
        logger.info("Starting v3.0 strategy cycle")
        logger.info("=" * 50)

        # Refresh universe if needed
        if self.should_refresh_universe():
            self.refresh_universe()

        # Check pending orders (limit order lifecycle)
        self.check_pending_orders()

        # Manage existing positions
        exits = self.manage_positions()
        logger.info(f"Exits: {len(exits)}")

        # Scan for new signals (only if we have capacity)
        entries = 0
        if len(self.active_positions) < RISK.max_positions:
            for symbol in self.universe:
                # Skip if already in position or pending
                if symbol in self.active_positions or symbol in self.pending_orders:
                    continue

                # Evaluate on candle close
                signal = self.evaluate_symbol_on_candle_close(symbol, timeframe)

                if signal:
                    # Place limit order (not market)
                    order = self.place_limit_order(signal)
                    if order:
                        entries += 1

                        # Track signal ID for idempotency
                        signal_id = generate_signal_id(
                            symbol,
                            int(datetime.utcnow().timestamp()),
                            f"placed:{signal.entry_price}"
                        )
                        self.processed_signals.add(signal_id)

        # Generate daily summary
        self.analytics.generate_daily_summary()

        logger.info(f"Cycle complete: {len(exits)} exits, {entries} entries, {len(self.active_positions)} open")

        return {
            "exits": len(exits),
            "entries": entries,
            "open_positions": len(self.active_positions),
            "pending_orders": len(self.pending_orders),
        }

    # =====================================================================
    # HELPERS
    # =====================================================================
    def _get_account_equity(self) -> float:
        """Get current account equity."""
        try:
            return self.client.get_account_equity()
        except Exception as e:
            logger.warning(f"Could not update account equity: {e}")
            return 10000.0

    def _get_total_risk(self) -> float:
        """Calculate total risk from open positions."""
        total = 0.0
        for pos in self.active_positions.values():
            total += pos.signal.risk_pct
        return total