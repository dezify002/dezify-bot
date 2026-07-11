"""
Layer 8: Analytics Engine.
Trade logging, performance tracking, and review cycle support.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from data.database import Database
from data.models import TradeRecord, TradeChecklist, DailyPerformance, RegimeLabel
from utils.logger import get_logger, log_trade_checklist
from config.settings import ANALYTICS

logger = get_logger(__name__)


class Analytics:
    """
    Layer 8 of the 8-layer architecture.
    Records every trade with full explainability and generates performance reports.
    """

    def __init__(self, database: Optional[Database] = None):
        self.db = database or Database()

    def record_trade_entry(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        position_size: float,
        position_value: float,
        leverage: int,
        risk_pct: float,
        stop_loss: float,
        take_profit: float,
        checklist: TradeChecklist,
        regime: str,
        adx: float,
        atr: float
    ) -> TradeRecord:
        """
        Record a complete trade entry with all 8-layer checklist data.
        This is your explainability schema in action.
        """
        trade = TradeRecord(
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            entry_time=datetime.utcnow(),
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            position_size=position_size,
            position_value_usd=position_value,
            leverage=leverage,
            risk_pct=risk_pct,
            market_regime=regime,
            adx_at_entry=adx,
            atr_at_entry=atr,
            checklist=checklist,
        )

        # Save to database
        self.db.save_trade(trade)

        # Log structured checklist
        log_trade_checklist(logger, trade_id, symbol, checklist.to_dict())

        logger.info(
            f"Trade recorded: {trade_id} | {symbol} {direction} | "
            f"Entry: {entry_price} | Risk: {risk_pct*100:.2f}% | Regime: {regime}"
        )

        return trade

    def record_regime_label(self, symbol: str, regime: str, adx: float, atr_pct: float, volume_vs_avg: float, daily_trend: str, fourh_trend: str):
        """
        Stamp regime label AT SIGNAL TIME.
        CRITICAL: Must be called before trade outcome is known.
        Prevents hindsight bias in analytics.
        """
        label = RegimeLabel(
            timestamp=datetime.utcnow(),
            symbol=symbol,
            regime=regime,
            adx=adx,
            atr_pct=atr_pct,
            volume_vs_avg=volume_vs_avg,
            daily_trend=daily_trend,
            fourh_trend=fourh_trend,
        )
        self.db.save_regime_label(label)
        return label

    def record_trade_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        fees: float = 0,
        funding: float = 0,
        slippage: float = 0
    ):
        """Record trade exit and calculate final metrics."""
        trade = self.db.get_trade(trade_id)
        if not trade:
            logger.error(f"Trade not found for exit: {trade_id}")
            return

        # Calculate P&L
        if trade.direction == "long":
            pnl = (exit_price - trade.entry_price) * trade.position_size
        else:
            pnl = (trade.entry_price - exit_price) * trade.position_size

        # Subtract costs
        pnl -= fees + funding

        # R multiple
        stop_distance = abs(trade.entry_price - trade.stop_loss_price)
        r_multiple = pnl / (trade.position_size * stop_distance) if stop_distance > 0 else 0

        # Expected vs actual
        expected_r = 2.0  # From strategy config
        if r_multiple >= expected_r * 0.8:
            expected_vs = "met"
        elif r_multiple > 0:
            expected_vs = "underperformed"
        else:
            expected_vs = "loss"

        # Update in DB
        self.db.update_trade_exit(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_time=datetime.utcnow(),
            realized_pnl=pnl,
            exit_fee=fees,
            slippage_exit=slippage,
            exit_reason=exit_reason
        )

        # Update checklist with exit reason
        trade.checklist.exit_reason = exit_reason
        self.db.save_trade(trade)

        # === PERFORMANCE LOGGER ===
        # Log ADX vs time-to-resolution for this trade
        try:
            from core.performance_logger import log_trade_resolution
            log_trade_resolution(trade, trade.checklist)
        except Exception as e:
            logger.warning(f"Performance logger failed: {e}")
        # ==========================

        logger.info(
            f"Exit recorded: {trade_id} | PnL: ${pnl:.2f} | "
            f"R: {r_multiple:+.2f} | Expected: {expected_vs}"
        )

    def generate_daily_summary(self, date: Optional[str] = None) -> DailyPerformance:
        """
        Generate daily performance summary.
        Called at end of trading day.
        """
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        # Get all closed trades for the day
        start = datetime.strptime(date, "%Y-%m-%d")
        end = start + timedelta(days=1)
        trades = self.db.get_trades_by_date_range(start, end)
        closed_trades = [t for t in trades if t.is_closed()]

        # Calculate metrics
        total = len(closed_trades)
        winners = sum(1 for t in closed_trades if t.is_winner())
        losers = total - winners

        gross_profit = sum(t.realized_pnl for t in closed_trades if t.realized_pnl > 0)
        gross_loss = sum(t.realized_pnl for t in closed_trades if t.realized_pnl < 0)
        net_pnl = gross_profit + gross_loss

        total_fees = sum(t.total_fees for t in closed_trades)
        total_funding = sum(t.funding_paid for t in closed_trades)

        # Get starting equity (from previous day or config)
        prev_perf = self.db.get_daily_performance(
            (start - timedelta(days=1)).strftime("%Y-%m-%d")
        )
        starting_equity = prev_perf.ending_equity if prev_perf else 10000  # Default

        ending_equity = starting_equity + net_pnl

        # Calculate drawdown
        peak = starting_equity
        max_dd_usd = 0
        running_equity = starting_equity

        for t in closed_trades:
            running_equity += t.realized_pnl
            if running_equity > peak:
                peak = running_equity
            dd = peak - running_equity
            if dd > max_dd_usd:
                max_dd_usd = dd

        max_dd_pct = max_dd_usd / peak if peak > 0 else 0

        # Sharpe (simplified - needs returns series)
        sharpe = None

        # Profit factor
        profit_factor = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')

        perf = DailyPerformance(
            date=date,
            starting_equity=starting_equity,
            ending_equity=ending_equity,
            total_trades=total,
            winning_trades=winners,
            losing_trades=losers,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            net_pnl=net_pnl,
            total_fees=total_fees,
            total_funding=total_funding,
            max_drawdown_pct=max_dd_pct,
            max_drawdown_usd=max_dd_usd,
            peak_equity=peak,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
        )

        self.db.save_daily_performance(perf)

        win_rate_str = f"{winners/total*100:.1f}%" if total > 0 else "N/A"
        logger.info(
            f"Daily summary {date}: Trades={total} | PnL=${net_pnl:+.2f} | "
            f"WinRate={win_rate_str} | PF={profit_factor:.2f} | "
            f"MaxDD={max_dd_pct*100:.2f}%"
        )

        return perf

    def get_regime_performance(self) -> List[Dict]:
        """
        Break down performance by market regime.
        Answers: Does the strategy rely on one specific condition?
        """
        return self.db.get_performance_by_regime()

    def get_recent_stats(self, days: int = 30) -> Dict:
        """Quick stats for dashboard/monitoring."""
        return self.db.get_recent_stats(days)

    def should_review(self) -> Dict:
        """
        Check if a review is due based on configured cadence.
        Returns review type if due, empty if not.
        """
        now = datetime.utcnow()

        # Weekly review
        if now.strftime("%A") == ANALYTICS.weekly_review_day:
            return {"due": True, "type": "weekly", "date": now.strftime("%Y-%m-%d")}

        # Monthly review
        if now.day == ANALYTICS.monthly_review_day:
            return {"due": True, "type": "monthly", "date": now.strftime("%Y-%m-%d")}

        # Quarterly review
        if now.month in ANALYTICS.quarterly_months and now.day == 1:
            return {"due": True, "type": "quarterly", "date": now.strftime("%Y-%m-%d")}

        return {"due": False}

    def generate_review_report(self, days: int = 7) -> Dict:
        """
        Generate a review report for the specified period.
        """
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        trades = self.db.get_trades_by_date_range(start, end)
        closed = [t for t in trades if t.is_closed()]

        if not closed:
            return {"error": "No trades in period"}

        # Performance by regime
        regime_perf = {}
        for t in closed:
            regime = t.market_regime or "unknown"
            if regime not in regime_perf:
                regime_perf[regime] = {"trades": 0, "pnl": 0, "winners": 0}
            regime_perf[regime]["trades"] += 1
            regime_perf[regime]["pnl"] += t.realized_pnl
            if t.is_winner():
                regime_perf[regime]["winners"] += 1

        # Layer failure analysis
        layer_failures = {"Layer1": 0, "Layer2": 0, "Layer3": 0, "Layer4": 0}
        for t in closed:
            if not t.is_winner():
                # Determine which layer failed (simplified heuristic)
                if not t.checklist.adx_above_threshold:
                    layer_failures["Layer1"] += 1
                elif not t.checklist.daily_ema_aligned:
                    layer_failures["Layer2"] += 1
                elif not t.checklist.pullback_confirmed:
                    layer_failures["Layer3"] += 1
                elif not t.checklist.volume_expansion:
                    layer_failures["Layer4"] += 1

        total_closed = len(closed)
        total_pnl = sum(t.realized_pnl for t in closed)

        return {
            "period_days": days,
            "total_trades": total_closed,
            "winners": sum(1 for t in closed if t.is_winner()),
            "win_rate": sum(1 for t in closed if t.is_winner()) / total_closed,
            "total_pnl": total_pnl,
            "avg_r": sum(t.r_multiple for t in closed) / total_closed,
            "regime_breakdown": regime_perf,
            "layer_failure_analysis": layer_failures,
            "recommendation": self._generate_recommendation(regime_perf, total_pnl),
        }

    def _generate_recommendation(self, regime_perf: Dict, total_pnl: float) -> str:
        """Generate a simple recommendation based on performance."""
        if total_pnl < 0:
            return "NEGATIVE: Review all layers. Consider reducing position size or tightening filters."

        # Check if performance is concentrated in one regime
        regime_pnl = {r: d["pnl"] for r, d in regime_perf.items()}
        if regime_pnl:
            best_regime = max(regime_pnl, key=regime_pnl.get)
            best_pct = regime_pnl[best_regime] / total_pnl if total_pnl > 0 else 0
            if best_pct > 0.8:
                return f"CONCENTRATED: {best_pct*100:.0f}% of profits from {best_regime}. Diversify or refine regime detector."

        return "BALANCED: Performance distributed across regimes. Continue current parameters."