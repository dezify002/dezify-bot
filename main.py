"""
Dezify Trading Bot - Main Entry Point
v3.0: Candle-close-driven evaluation using APScheduler
"""

import sys
import time
import signal
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from strategies.trend_pullback_v3 import TrendPullbackStrategy
from utils.logger import get_logger

logger = get_logger(__name__)

# Global strategy instance
_strategy = None
_scheduler = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info("Shutdown signal received, stopping scheduler...")
    if _scheduler:
        _scheduler.shutdown(wait=False)
    sys.exit(0)


def run_strategy_cycle(timeframe: str = "1H"):
    """Called by APScheduler when a candle closes."""
    global _strategy

    if _strategy is None:
        logger.error("Strategy not initialized")
        return

    try:
        logger.info(f"Running strategy cycle for {timeframe} candle close")
        result = _strategy.run_cycle(timeframe=timeframe)
        logger.info(f"Cycle result: {result}")
    except Exception as e:
        logger.error(f"Strategy cycle error: {e}", exc_info=True)


def main():
    global _strategy, _scheduler

    parser = argparse.ArgumentParser(description="Dezify Trading Bot")
    parser.add_argument("--mode", choices=["paper", "live", "backtest"], 
                        default="paper", help="Trading mode")
    parser.add_argument("--timeframe", default="1H", 
                        choices=["15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D"],
                        help="Candle timeframe for evaluation")
    parser.add_argument("--start-date", default="2024-01-01",
                        help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default="2024-12-31",
                        help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--initial-equity", type=float, default=10000,
                        help="Initial equity for backtest")
    args = parser.parse_args()

    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info("=" * 60)
    logger.info("DEZIFY TRADING BOT v3.0")
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Timeframe: {args.timeframe}")
    logger.info("=" * 60)

    if args.mode == "backtest":
        from backtest.engine import BacktestEngine
        logger.info(f"Running backtest: {args.start_date} to {args.end_date}")
        engine = BacktestEngine(args.start_date, args.end_date, args.initial_equity)
        result = engine.run()
        logger.info(f"Backtest complete: Return={result.total_return_pct:.2f}%")
        return

    # Initialize strategy
    logger.info("Initializing strategy...")
    _strategy = TrendPullbackStrategy()
    _strategy.refresh_universe()

    if args.mode == "paper":
        logger.info("Running in PAPER mode")
    elif args.mode == "live":
        logger.info("Running in LIVE mode")

    # Setup APScheduler for candle-close timing
    _scheduler = BackgroundScheduler()

    # Map timeframe to cron expression
    cron_map = {
        "15m": "*/15 * * * *",      # Every 15 minutes
        "30m": "*/30 * * * *",      # Every 30 minutes
        "1H": "0 * * * *",          # Every hour at :00
        "2H": "0 */2 * * *",        # Every 2 hours
        "4H": "0 */4 * * *",        # Every 4 hours
        "6H": "0 */6 * * *",        # Every 6 hours
        "12H": "0 */12 * * *",      # Every 12 hours
        "1D": "0 0 * * *",          # Daily at midnight
    }

    cron_expr = cron_map.get(args.timeframe, "0 * * * *")

    logger.info(f"Scheduling cycles with cron: {cron_expr}")

    _scheduler.add_job(
        run_strategy_cycle,
        trigger=CronTrigger.from_crontab(cron_expr),
        kwargs={"timeframe": args.timeframe},
        id="strategy_cycle",
        replace_existing=True,
        max_instances=1,  # Prevent overlapping runs
    )

    _scheduler.start()
    logger.info("Scheduler started. Waiting for candle closes...")

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        signal_handler(signal.SIGINT, None)


if __name__ == "__main__":
    main()