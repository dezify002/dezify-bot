"""
Paper trading runner for the trend-pullback strategy.
Simulates execution without real money. Validates behavior before live deployment.
NOW WITH: Scan logging to dashboard visibility
"""

import time
import json
import os
from datetime import datetime, timezone
from typing import Dict, List

from strategies.trend_pullback_v3 import TrendPullbackStrategy
from core.execution_engine import ExecutionEngine
from data.database import Database
from utils.logger import get_logger
from config.secrets import is_paper

logger = get_logger(__name__)

# Scan logging paths (same as dashboard)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
SCAN_LOG_FILE = os.path.join(DATA_DIR, "scan_log.json")
SIGNAL_LOG_FILE = os.path.join(DATA_DIR, "signal_log.json")


def _log_scan(symbol: str, layer: str, passed: bool, reason: str, metadata: Dict = None):
    """Log a single symbol's evaluation result for dashboard visibility."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "layer": layer,
        "passed": passed,
        "reason": reason,
        "metadata": metadata or {},
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        logs = []
        if os.path.exists(SCAN_LOG_FILE):
            with open(SCAN_LOG_FILE, "r") as f:
                logs = json.load(f)
        logs.append(entry)
        logs = logs[-5000:]  # Keep last 5000
        with open(SCAN_LOG_FILE, "w") as f:
            json.dump(logs, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Failed to write scan log: {e}")


def _log_signal(signal_data: Dict):
    """Log a generated trading signal for dashboard visibility."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **signal_data,
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        logs = []
        if os.path.exists(SIGNAL_LOG_FILE):
            with open(SIGNAL_LOG_FILE, "r") as f:
                logs = json.load(f)
        logs.append(entry)
        logs = logs[-1000:]  # Keep last 1000
        with open(SIGNAL_LOG_FILE, "w") as f:
            json.dump(logs, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Failed to write signal log: {e}")


class PaperTrader:
    """
    Paper trading environment.
    Tracks simulated positions, P&L, and execution quality.
    NOW logs all scans and signals to dashboard-visible files.
    """

    def __init__(self, initial_equity: float = 10000.0):
        if not is_paper():
            logger.warning("Not in paper mode - set TRADING_MODE=paper in .env")

        self.strategy = TrendPullbackStrategy()
        self.execution = ExecutionEngine()
        self.db = Database(db_path="data/paper_trades.db")

        # Paper state
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.paper_positions: Dict[str, Dict] = {}  # symbol -> position
        self.trade_count = 0
        self.start_time = datetime.utcnow()

        logger.info(f"Paper trader initialized | Equity: ${initial_equity}")

    def run(self, cycles: int = None, interval_seconds: int = 3600):
        """
        Run paper trading for specified cycles or indefinitely.

        Args:
            cycles: Number of cycles to run (None = infinite)
            interval_seconds: Seconds between cycles (default 1 hour)
        """
        logger.info("=" * 60)
        logger.info("PAPER TRADING STARTED")
        logger.info("=" * 60)

        cycle = 0

        try:
            while cycles is None or cycle < cycles:
                cycle += 1
                now = datetime.utcnow()

                logger.info(f"\n{'='*40}")
                logger.info(f"Paper Cycle {cycle} | {now.strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*40}")

                # Refresh universe and log it
                try:
                    self.strategy.refresh_universe()
                    universe_size = len(self.strategy.universe)
                    logger.info(f"Universe refreshed: {universe_size} symbols")
                    _log_scan("SYSTEM", "universe_refresh", True, 
                              f"Loaded {universe_size} symbols",
                              {"universe": self.strategy.universe[:20]})
                except Exception as e:
                    logger.warning(f"Universe refresh failed: {e}")
                    _log_scan("SYSTEM", "universe_refresh", False, str(e))

                # Evaluate each symbol and log results
                signals_found = 0
                for symbol in self.strategy.universe:
                    try:
                        signal = self.strategy.evaluate_symbol(symbol)
                        if signal:
                            signals_found += 1
                            _log_signal({
                                "symbol": signal.symbol,
                                "direction": signal.direction,
                                "entry_price": signal.entry_price,
                                "stop_loss": signal.stop_loss,
                                "take_profit": signal.take_profit,
                                "confidence": signal.confidence,
                                "signal_id": signal.signal_id,
                                "cycle": cycle,
                            })
                            logger.info(f"SIGNAL: {signal.symbol} {signal.direction} @ {signal.entry_price:.4f}")
                    except Exception as e:
                        _log_scan(symbol, "evaluation_error", False, str(e))

                # Run strategy cycle
                result = self.strategy.run_cycle()

                # Log cycle completion
                _log_scan("SYSTEM", "cycle_complete", True, 
                          f"Cycle {cycle} complete",
                          {"result": result, "signals_found": signals_found,
                           "universe_size": len(self.strategy.universe)})

                # Track paper metrics
                self._update_paper_state()

                # Log status
                status = self.get_status()
                logger.info(f"Paper equity: ${status['equity']:.2f}")
                logger.info(f"Open positions: {status['open_positions']}")
                logger.info(f"Total trades: {status['total_trades']}")

                # Check promotion criteria
                self._check_promotion_gates()

                if cycles and cycle >= cycles:
                    break

                logger.info(f"Sleeping {interval_seconds}s...")
                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("Paper trading stopped by user")

        # Final report
        self._generate_final_report()

    def _update_paper_state(self):
        """Update paper trading state from strategy."""
        for symbol, position in list(self.paper_positions.items()):
            current_price = self.strategy.market_data.get_latest_price(symbol)
            if not current_price:
                continue

            if position["direction"] == "long":
                unrealized = (current_price - position["entry_price"]) * position["size"]
            else:
                unrealized = (position["entry_price"] - current_price) * position["size"]

            position["unrealized_pnl"] = unrealized
            position["current_price"] = current_price

    def _check_promotion_gates(self):
        """Check if paper trading meets promotion criteria."""
        duration_days = (datetime.utcnow() - self.start_time).days

        logger.info(
            f"Promotion progress: {self.trade_count}/{50} trades | "
            f"{duration_days}/14 days"
        )

        if self.trade_count >= 50 and duration_days >= 14:
            logger.info("PAPER TRADING PROMOTION CRITERIA MET")
            logger.info("Strategy is eligible for small live deployment")

    def get_status(self) -> Dict:
        """Get current paper trading status."""
        closed_trades = self.db.get_all_trades(limit=1000)
        closed = [t for t in closed_trades if t.is_closed()]

        total_pnl = sum(t.realized_pnl for t in closed)
        current_equity = self.initial_equity + total_pnl

        unrealized = sum(p.get("unrealized_pnl", 0) for p in self.paper_positions.values())

        return {
            "mode": "paper",
            "equity": current_equity + unrealized,
            "realized_pnl": total_pnl,
            "unrealized_pnl": unrealized,
            "open_positions": len(self.paper_positions),
            "total_trades": len(closed),
            "winners": sum(1 for t in closed if t.is_winner()),
            "duration_days": (datetime.utcnow() - self.start_time).days,
        }

    def _generate_final_report(self):
        """Generate final paper trading report."""
        status = self.get_status()
        closed = self.db.get_all_trades(limit=1000)
        closed_trades = [t for t in closed if t.is_closed()]

        total = len(closed_trades)
        if total == 0:
            logger.info("No trades completed in paper period")
            return

        win_rate = sum(1 for t in closed_trades if t.is_winner()) / total
        avg_r = sum(t.r_multiple for t in closed_trades) / total
        total_fees = sum(t.total_fees for t in closed_trades)

        print("\n" + "=" * 60)
        print("PAPER TRADING FINAL REPORT")
        print("=" * 60)
        print(f"Duration: {status['duration_days']} days")
        print(f"Total Trades: {total}")
        print(f"Win Rate: {win_rate*100:.1f}%")
        print(f"Realized P&L: ${status['realized_pnl']:+.2f}")
        print(f"Total Fees: ${total_fees:.2f}")
        print(f"Average R: {avg_r:+.2f}")
        print(f"Final Equity: ${status['equity']:.2f}")
        print("=" * 60)

        # Save report
        import json
        import os
        os.makedirs("paper_results", exist_ok=True)

        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "status": status,
            "trades": [t.to_dict() for t in closed_trades],
        }

        filename = f"paper_results/paper_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info(f"Paper report saved: {filename}")


def main():
    """Run paper trading from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="Paper trade the strategy")
    parser.add_argument("--equity", type=float, default=10000, help="Starting equity")
    parser.add_argument("--cycles", type=int, default=None, help="Number of cycles")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between cycles")
    args = parser.parse_args()

    trader = PaperTrader(initial_equity=args.equity)
    trader.run(cycles=args.cycles, interval_seconds=args.interval)


if __name__ == "__main__":
    main()