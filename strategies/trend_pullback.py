"""
Trend-Pullback Strategy (8-Layer Architecture)
Layer 5: Signal Generator
"""

import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from core.bitget_client import BitgetClient
from core.market_data import MarketData
from core.regime_detector import RegimeDetector
from core.trend_analyzer import TrendAnalyzer
from core.pullback_detector import PullbackDetector
from core.entry_trigger import EntryTrigger
from core.position_sizer import PositionSizer
from core.execution_engine import ExecutionEngine
from core.analytics import Analytics
from data.models import TradeChecklist, Signal
from utils.logger import get_logger
from config.settings import STRATEGY, RISK

logger = get_logger(__name__)


class TrendPullbackStrategy:
    """
    Complete trend-pullback strategy implementing all 8 layers:
    1. Regime Filter      2. Trend Analyzer    3. Pullback Detector
    4. Entry Trigger      5. Signal Generator  6. Position Sizer
    7. Execution Engine    8. Analytics
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
        self.open_positions: Dict[str, dict] = {}
        
        logger.info("TrendPullbackStrategy initialized")
    
    def refresh_universe(self) -> List[str]:
        """
        Refresh the trading universe from Bitget.
        Filters for liquid USDT perpetual futures.
        """
        logger.info("Refreshing trading universe...")
        
        try:
            # Fetch all tickers
            tickers = self.client.get_tickers(product_type="USDT-FUTURES")
            
            if not tickers:
                logger.warning("No tickers returned from API")
                return self.universe
            
            symbols = []
            for t in tickers:
                symbol = t.get("symbol", "")
                
                # Only USDT perpetual futures
                if not symbol.endswith("USDT"):
                    continue
                
                # Skip blacklisted non-crypto symbols
                if symbol in STRATEGY.symbol_blacklist:
                    continue
                
                # Check minimum volume
                vol_24h = float(t.get("usdtVolume", 0))
                if vol_24h < STRATEGY.min_volume_24h:
                    continue
                
                symbols.append(symbol)
            
            self.universe = sorted(symbols)
            self.last_universe_refresh = datetime.utcnow()
            
            logger.info(f"Universe refreshed: {len(self.universe)} symbols")
            for s in self.universe:
                logger.debug(f"  - {s}")
            
            return self.universe
            
        except Exception as e:
            logger.error(f"Failed to refresh universe: {e}")
            return self.universe
    
    def should_refresh_universe(self) -> bool:
        """Check if universe refresh is due."""
        if not self.last_universe_refresh:
            return True
        
        elapsed = datetime.utcnow() - self.last_universe_refresh
        return elapsed > timedelta(minutes=STRATEGY.universe_refresh_interval_minutes)
    
    def scan_for_signals(self) -> List[Signal]:
        """
        Scan universe for valid trade signals.
        Returns list of signals that passed all layers.
        """
        if self.should_refresh_universe():
            self.refresh_universe()
        
        signals = []
        
        for symbol in self.universe:
            # Skip if already in a position
            if symbol in self.open_positions:
                continue
            
            signal = self.evaluate_symbol(symbol)
            if signal:
                signals.append(signal)
        
        logger.info(f"Scan complete: {len(signals)} valid signals")
        return signals
    
    def evaluate_symbol(self, symbol: str) -> Optional[Signal]:
        """
        Run all 8 layers on a single symbol.
        Returns Signal if all checks pass, None otherwise.
        """
        checklist = TradeChecklist()
        
        # ─── Layer 1: Regime Filter ─────────────────────────────
        regime = self.regime_detector.evaluate(symbol)
        checklist.adx_above_threshold = regime["tradable"]
        checklist.market_regime = regime["regime"]
        checklist.adx_value = regime["adx"]
        
        if not regime["tradable"]:
            return None
        
        # ─── Layer 2: Trend Analyzer ────────────────────────────
        trend = self.trend_analyzer.analyze(symbol)
        checklist.daily_ema_aligned = trend["daily_aligned"]
        checklist.fourh_ema_aligned = trend["fourh_aligned"]
        checklist.trend_direction = trend["direction"]
        checklist.trend_score = trend["score"]
        
        # In soft mode: need at least weak alignment in trend direction
        # In hard mode: both timeframes must fully align (handled in analyzer)
        if trend["score"] <= 0:
            return None
        
        # Determine trade direction from trend
        direction = None
        if trend["direction"] in ("up", "up_weak"):
            direction = "long"
        elif trend["direction"] in ("down", "down_weak"):
            direction = "short"
        
        if not direction:
            return None
        
        # ─── Layer 3: Pullback Detector ─────────────────────────
        pullback = self.pullback_detector.detect(symbol, direction)
        checklist.pullback_confirmed = pullback["valid"]
        checklist.pullback_depth = pullback.get("depth")
        
        if not pullback["valid"]:
            return None
        
        # ─── Layer 4: Entry Trigger ─────────────────────────────
        trigger = self.entry_trigger.check(symbol, direction, pullback)
        checklist.entry_triggered = trigger["triggered"]
        checklist.entry_price = trigger.get("entry_price")
        checklist.stop_loss_price = trigger.get("stop_loss")
        checklist.take_profit_price = trigger.get("take_profit")
        
        if not trigger["triggered"]:
            return None
        
        # ─── Layer 5: Signal Generator ──────────────────────────
        # Calculate position size
        account_equity = self._get_account_equity()
        position = self.position_sizer.calculate(
            equity=account_equity,
            entry_price=trigger["entry_price"],
            stop_loss=trigger["stop_loss"],
            direction=direction,
            atr=pullback.get("atr", 0),
        )
        
        checklist.position_size = position["size"]
        checklist.position_value = position["value"]
        checklist.leverage = position["leverage"]
        checklist.risk_pct = position["risk_pct"]
        
        # Validate risk limits
        if position["risk_pct"] > RISK.max_account_risk_per_trade:
            logger.warning(f"{symbol}: Risk {position['risk_pct']*100:.2f}% exceeds max")
            return None
        
        # Check total portfolio risk
        total_risk = self._get_total_risk() + position["risk_pct"]
        if total_risk > RISK.max_account_risk_total:
            logger.warning(f"{symbol}: Total risk {total_risk*100:.2f}% exceeds max")
            return None
        
        # Check max positions
        if len(self.open_positions) >= RISK.max_positions:
            logger.debug(f"{symbol}: Max positions reached")
            return None
        
        # ─── Build Signal ───────────────────────────────────────
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
        
        return signal
    
    def execute_signal(self, signal: Signal) -> bool:
        """
        Execute a signal (Layer 6-7).
        """
        # ─── Layer 6: Position Sizing (already done in signal) ──
        # ─── Layer 7: Execution Engine ──────────────────────────
        
        trade_id = str(uuid.uuid4())[:8]
        
        try:
            # Record regime label BEFORE execution (anti-hindsight)
            self.analytics.record_regime_label(
                symbol=signal.symbol,
                regime=signal.checklist.market_regime,
                adx=signal.checklist.adx_value,
                atr_pct=signal.checklist.pullback_depth or 0,
                volume_vs_avg=1.0,  # Simplified
                daily_trend=signal.checklist.trend_direction,
                fourh_trend=signal.checklist.trend_direction,
            )
            
            # Execute order
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
                # Record trade entry
                self.analytics.record_trade_entry(
                    trade_id=trade_id,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    entry_price=signal.entry_price,
                    position_size=signal.position_size,
                    position_value=signal.position_value,
                    leverage=signal.leverage,
                    risk_pct=signal.risk_pct,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    checklist=signal.checklist,
                    regime=signal.checklist.market_regime,
                    adx=signal.checklist.adx_value,
                    atr=signal.checklist.pullback_depth or 0,
                )
                
                self.open_positions[signal.symbol] = {
                    "trade_id": trade_id,
                    "signal": signal,
                    "entry_time": datetime.utcnow(),
                }
                
                logger.info(
                    f"Trade executed: {trade_id} | {signal.symbol} {signal.direction} | "
                    f"Entry: {signal.entry_price} | Size: {signal.position_size} | "
                    f"Leverage: {signal.leverage}x"
                )
                return True
            else:
                logger.error(f"Order failed: {result.get('error')}")
                return False
                
        except Exception as e:
            logger.error(f"Execution error for {signal.symbol}: {e}")
            return False
    
    def check_exits(self):
        """
        Check open positions for exit conditions.
        """
        exited = []
        
        for symbol, pos in list(self.open_positions.items()):
            # Get current price
            current = self.market_data.get_latest_price(symbol)
            signal = pos["signal"]
            
            should_exit = False
            exit_price = current
            exit_reason = ""
            
            # Stop loss hit
            if signal.direction == "long":
                if current <= signal.stop_loss:
                    should_exit = True
                    exit_price = signal.stop_loss
                    exit_reason = "stop_loss"
                elif current >= signal.take_profit:
                    should_exit = True
                    exit_price = signal.take_profit
                    exit_reason = "take_profit"
            else:
                if current >= signal.stop_loss:
                    should_exit = True
                    exit_price = signal.stop_loss
                    exit_reason = "stop_loss"
                elif current <= signal.take_profit:
                    should_exit = True
                    exit_price = signal.take_profit
                    exit_reason = "take_profit"
            
            # Time-based exit (max hold time)
            hold_time = datetime.utcnow() - pos["entry_time"]
            if hold_time > timedelta(hours=48):
                should_exit = True
                exit_reason = "time_exit"
            
            if should_exit:
                self._exit_position(symbol, exit_price, exit_reason)
                exited.append(symbol)
        
        return exited
    
    def _exit_position(self, symbol: str, exit_price: float, reason: str):
        """Close a position and record the exit."""
        pos = self.open_positions.pop(symbol, None)
        if not pos:
            return
        
        trade_id = pos["trade_id"]
        
        try:
            self.execution.close_position(symbol)
            
            self.analytics.record_trade_exit(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_reason=reason,
            )
            
            logger.info(f"Position closed: {symbol} | Reason: {reason} | Price: {exit_price}")
            
        except Exception as e:
            logger.error(f"Exit error for {symbol}: {e}")
    
    def _get_account_equity(self) -> float:
        """Get current account equity."""
        try:
            return self.client.get_account_equity()
        except Exception as e:
            logger.warning(f"Could not update account equity: {e}")
            return 10000.0  # Fallback for paper mode
    
    def _get_total_risk(self) -> float:
        """Calculate total risk from open positions."""
        total = 0.0
        for pos in self.open_positions.values():
            total += pos["signal"].risk_pct
        return total
    
    def run_cycle(self):
        """
        Run one complete strategy cycle.
        """
        logger.info("=" * 50)
        logger.info("Starting strategy cycle")
        logger.info("=" * 50)
        
        # Update account equity
        equity = self._get_account_equity()
        logger.info(f"Account equity: ${equity:,.2f}")
        
        # Refresh universe if needed
        if self.should_refresh_universe():
            self.refresh_universe()
        
        # Check exits first
        exits = self.check_exits()
        logger.info(f"Exits: {len(exits)}")
        
        # Scan for new signals
        signals = self.scan_for_signals()
        
        # Execute signals
        entries = 0
        for signal in signals:
            if self.execute_signal(signal):
                entries += 1
        
        # Generate daily summary
        self.analytics.generate_daily_summary()
        
        logger.info(f"Cycle complete: {len(exits)} exits, {entries} entries")
        return {
            "exits": len(exits),
            "entries": entries,
            "open_positions": len(self.open_positions),
        }