"""
Paper trading runner for the trend-pullback strategy.
Simulates execution without real money. Validates behavior before live deployment.
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List

from strategies.trend_pullback_v3 import TrendPullbackStrategy
from core.execution_engine import ExecutionEngine
from data.database import Database
from utils.logger import get_logger
from config.secrets import is_paper

logger = get_logger(__name__)


class PaperTrader:
    """
    Paper trading environment.
    Tracks simulated positions, P&L, and execution quality.
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
                
                # Run strategy cycle
                result = self.strategy.run_cycle()
                
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
        # In paper mode, execution engine returns simulated fills
        # We track these as if they were real
        
        for symbol, position in list(self.paper_positions.items()):
            current_price = self.strategy.market_data.get_latest_price(symbol)
            if not current_price:
                continue
            
            # Update unrealized P&L
            if position["direction"] == "long":
                unrealized = (current_price - position["entry_price"]) * position["size"]
            else:
                unrealized = (position["entry_price"] - current_price) * position["size"]
            
            position["unrealized_pnl"] = unrealized
            position["current_price"] = current_price
    
    def _check_promotion_gates(self):
        """Check if paper trading meets promotion criteria."""
        duration_days = (datetime.utcnow() - self.start_time).days
        
        # Log progress toward promotion
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
        
        # Add unrealized
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