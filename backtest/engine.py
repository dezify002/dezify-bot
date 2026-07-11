"""
Backtest Engine for TrendPullbackStrategy
Bar-by-bar historical simulation - STANDALONE VERSION
No imports from project modules that could fail.
"""

import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

# Only import things that we know work (from the bot modules import)
# We avoid importing config.settings at module level
from strategies.trend_pullback import TrendPullbackStrategy
from core.market_data import MarketData
from core.bitget_client import BitgetClient
from data.database import Database
from data.models import Signal, TradeChecklist
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestTrade:
    """Record of a single backtest trade."""
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    position_size: float = 0.0
    position_value: float = 0.0
    leverage: float = 1.0
    risk_pct: float = 0.0
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    realized_pnl: float = 0.0
    realized_pnl_pct: float = 0.0
    r_multiple: float = 0.0
    market_regime: str = ""
    checklist: Optional[TradeChecklist] = None

    def is_closed(self) -> bool:
        return self.exit_price is not None

    def is_winner(self) -> bool:
        return self.realized_pnl > 0


@dataclass  
class BacktestResult:
    """Complete backtest results."""
    start_date: str
    end_date: str
    initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_r: float
    avg_winner_r: float
    avg_loser_r: float
    profit_factor: float
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)


class HistoricalMarketData:
    """MarketData wrapper that serves historical snapshots."""

    def __init__(self, historical_candles: Dict[str, List[dict]]):
        self.candles = historical_candles
        self.current_index = 0
        self.current_time: Optional[datetime] = None

    def set_index(self, index: int):
        self.current_index = index
        for symbol, bars in self.candles.items():
            if index < len(bars):
                ts = bars[index].get("timestamp") or bars[index].get("time")
                if ts:
                    if isinstance(ts, str):
                        self.current_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    else:
                        self.current_time = ts
                break

    def get_candles(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> List[dict]:
        if symbol not in self.candles:
            return []
        bars = self.candles[symbol]
        end_idx = min(self.current_index + 1, len(bars))
        start_idx = max(0, end_idx - limit)
        return bars[start_idx:end_idx]

    def get_latest_price(self, symbol: str) -> Optional[float]:
        if symbol not in self.candles:
            return None
        bars = self.candles[symbol]
        if self.current_index >= len(bars):
            return None
        bar = bars[self.current_index]
        return float(bar.get("close", bar.get("c", 0)))

    def get_volume_24h(self, symbol: str) -> float:
        candles = self.get_candles(symbol, timeframe="1h", limit=24)
        return sum(float(c.get("volume", c.get("v", 0))) for c in candles)


class BacktestEngine:
    """Bar-by-bar backtest engine."""

    def __init__(self, start_date: str, end_date: str, initial_equity: float = 10000.0):
        self.start_date = datetime.strptime(start_date, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date, "%Y-%m-%d")
        self.initial_equity = initial_equity
        self.equity = initial_equity

        self.trades: List[BacktestTrade] = []
        self.open_positions: Dict[str, BacktestTrade] = {}
        self.equity_curve: List[Tuple[datetime, float]] = []

        self.market_data = MarketData()
        self.client = BitgetClient()

        logger.info(f"BacktestEngine: {start_date} to {end_date}, equity=${initial_equity}")

    def _get_settings(self):
        """Lazy import settings - returns defaults if import fails."""
        try:
            from config.settings import STRATEGY, RISK
            return STRATEGY, RISK
        except Exception as e:
            logger.warning(f"Could not import settings, using defaults: {e}")
            # Create minimal defaults
            class DefaultStrategy:
                symbol_blacklist = []
                min_volume_24h = 1000000
                universe_refresh_interval_minutes = 60

            class DefaultRisk:
                max_positions = 3
                max_account_risk_per_trade = 0.02
                max_account_risk_total = 0.05

            return DefaultStrategy(), DefaultRisk()

    def fetch_historical_data(self, symbols: List[str], timeframe: str = "1h") -> Dict[str, List[dict]]:
        candles = {}
        for symbol in symbols:
            try:
                bars = self.client.get_candles(
                    symbol=symbol,
                    granularity=timeframe,
                    start_time=int(self.start_date.timestamp() * 1000),
                    end_time=int(self.end_date.timestamp() * 1000),
                )
                if bars and len(bars) > 50:
                    candles[symbol] = bars
                    logger.info(f"  {symbol}: {len(bars)} bars")
                else:
                    logger.warning(f"  {symbol}: insufficient data ({len(bars) if bars else 0} bars)")
            except Exception as e:
                logger.warning(f"  {symbol}: fetch failed - {e}")
        return candles

    def run(self) -> BacktestResult:
        STRATEGY, RISK = self._get_settings()

        logger.info("=" * 60)
        logger.info("STARTING BACKTEST")
        logger.info("=" * 60)

        # Get universe
        logger.info("Fetching universe...")
        try:
            tickers = self.client.get_tickers(product_type="USDT-FUTURES")
            symbols = []
            for t in tickers:
                symbol = t.get("symbol", "")
                if symbol.endswith("USDT") and symbol not in STRATEGY.symbol_blacklist:
                    vol = float(t.get("usdtVolume", 0))
                    if vol >= STRATEGY.min_volume_24h:
                        symbols.append(symbol)
            symbols = sorted(symbols)[:20]
            logger.info(f"Universe: {len(symbols)} symbols")
        except Exception as e:
            logger.error(f"Failed to fetch universe: {e}")
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

        # Fetch historical data
        logger.info("Fetching historical candles...")
        historical_candles = self.fetch_historical_data(symbols, timeframe="1h")

        if not historical_candles:
            logger.error("No historical data available")
            return self._build_result()

        min_bars = min(len(bars) for bars in historical_candles.values())
        logger.info(f"Simulation: {min_bars} bars")

        hist_md = HistoricalMarketData(historical_candles)
        strategy = TrendPullbackStrategy()
        strategy.market_data = hist_md

        # Bar-by-bar simulation
        for bar_idx in range(50, min_bars):
            hist_md.set_index(bar_idx)
            current_time = hist_md.current_time
            if current_time is None:
                continue

            self.equity_curve.append((current_time, self.equity))
            self._check_exits_bar(hist_md, current_time)

            if bar_idx % 4 == 0:
                self._scan_for_entries(strategy, hist_md, symbols, current_time, RISK)

        # Close remaining
        for symbol, trade in list(self.open_positions.items()):
            last_price = hist_md.get_latest_price(symbol)
            if last_price:
                self._close_trade(trade, last_price, current_time, "backtest_end")

        logger.info("=" * 60)
        logger.info("BACKTEST COMPLETE")
        logger.info("=" * 60)

        return self._build_result()

    def _check_exits_bar(self, hist_md, current_time):
        for symbol, trade in list(self.open_positions.items()):
            current_price = hist_md.get_latest_price(symbol)
            if current_price is None:
                continue

            should_exit = False
            exit_price = current_price
            exit_reason = ""

            if trade.direction == "long":
                if current_price <= trade.stop_loss:
                    should_exit = True
                    exit_price = trade.stop_loss
                    exit_reason = "stop_loss"
                elif current_price >= trade.take_profit:
                    should_exit = True
                    exit_price = trade.take_profit
                    exit_reason = "take_profit"
            else:
                if current_price >= trade.stop_loss:
                    should_exit = True
                    exit_price = trade.stop_loss
                    exit_reason = "stop_loss"
                elif current_price <= trade.take_profit:
                    should_exit = True
                    exit_price = trade.take_profit
                    exit_reason = "take_profit"

            if trade.entry_time and (current_time - trade.entry_time) > timedelta(hours=48):
                should_exit = True
                exit_reason = "time_exit"

            if should_exit:
                self._close_trade(trade, exit_price, current_time, exit_reason)

    def _scan_for_entries(self, strategy, hist_md, symbols, current_time, RISK):
        for symbol in symbols:
            if symbol in self.open_positions:
                continue
            if len(self.open_positions) >= RISK.max_positions:
                break

            try:
                signal = strategy.evaluate_symbol(symbol)
                if signal:
                    fill_price = signal.entry_price
                    slippage = fill_price * 0.0005
                    if signal.direction == "long":
                        fill_price += slippage
                    else:
                        fill_price -= slippage

                    margin_needed = signal.position_value / signal.leverage
                    if margin_needed > self.equity * 0.95:
                        continue

                    trade = BacktestTrade(
                        trade_id=str(uuid.uuid4())[:8],
                        symbol=symbol,
                        direction=signal.direction,
                        entry_price=fill_price,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        position_size=signal.position_size,
                        position_value=signal.position_value,
                        leverage=signal.leverage,
                        risk_pct=signal.risk_pct,
                        entry_time=current_time,
                        market_regime=signal.checklist.market_regime if signal.checklist else "",
                        checklist=signal.checklist,
                    )

                    self.trades.append(trade)
                    self.open_positions[symbol] = trade

                    logger.info(
                        f"ENTRY: {trade.trade_id} {symbol} {signal.direction} "
                        f"@ {fill_price:.4f} | Size: {signal.position_size:.4f} | "
                        f"Risk: {signal.risk_pct*100:.2f}%"
                    )
            except Exception as e:
                logger.debug(f"Error evaluating {symbol}: {e}")

    def _close_trade(self, trade, exit_price, exit_time, reason):
        trade.exit_price = exit_price
        trade.exit_time = exit_time
        trade.exit_reason = reason

        if trade.direction == "long":
            trade.realized_pnl = (exit_price - trade.entry_price) * trade.position_size
            trade.realized_pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
        else:
            trade.realized_pnl = (trade.entry_price - exit_price) * trade.position_size
            trade.realized_pnl_pct = (trade.entry_price - exit_price) / trade.entry_price

        risk_amount = abs(trade.entry_price - trade.stop_loss) * trade.position_size
        if risk_amount > 0:
            trade.r_multiple = trade.realized_pnl / risk_amount

        self.equity += trade.realized_pnl

        if trade.symbol in self.open_positions:
            del self.open_positions[trade.symbol]

        logger.info(
            f"EXIT: {trade.trade_id} {trade.symbol} | "
            f"Reason: {reason} | PnL: ${trade.realized_pnl:.2f} | "
            f"R: {trade.r_multiple:.2f}"
        )

    def _build_result(self) -> BacktestResult:
        closed_trades = [t for t in self.trades if t.is_closed()]
        winners = [t for t in closed_trades if t.is_winner()]
        losers = [t for t in closed_trades if not t.is_winner()]

        total_r = sum(t.r_multiple for t in closed_trades) if closed_trades else 0
        avg_r = total_r / len(closed_trades) if closed_trades else 0

        avg_winner_r = sum(t.r_multiple for t in winners) / len(winners) if winners else 0
        avg_loser_r = sum(t.r_multiple for t in losers) / len(losers) if losers else 0

        total_profit = sum(t.realized_pnl for t in winners)
        total_loss = abs(sum(t.realized_pnl for t in losers))
        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')

        max_dd = 0.0
        peak = self.initial_equity
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd

        total_return = (self.equity - self.initial_equity) / self.initial_equity

        return BacktestResult(
            start_date=self.start_date.strftime("%Y-%m-%d"),
            end_date=self.end_date.strftime("%Y-%m-%d"),
            initial_equity=self.initial_equity,
            final_equity=self.equity,
            total_return_pct=total_return * 100,
            max_drawdown_pct=max_dd * 100,
            total_trades=len(closed_trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate=(len(winners) / len(closed_trades) * 100) if closed_trades else 0,
            avg_r=avg_r,
            avg_winner_r=avg_winner_r,
            avg_loser_r=avg_loser_r,
            profit_factor=profit_factor,
            trades=self.trades,
            equity_curve=self.equity_curve,
        )