"""
Main entry point for the crypto perpetual futures bot.
Handles initialization, mode selection, and the main loop.
"""

import argparse
import sys
import time
from datetime import datetime

from config.settings import validate_config, TRADING_MODE
from strategies.trend_pullback import TrendPullbackStrategy
from utils.logger import get_logger

logger = get_logger(__name__)


def get_trading_mode():
    """Get trading mode from env or default."""
    return TRADING_MODE


def is_live() -> bool:
    """Check if running in live mode."""
    return TRADING_MODE == "live"


def is_paper() -> bool:
    """Check if running in paper mode."""
    return TRADING_MODE == "paper"


def is_backtest() -> bool:
    """Check if running in backtest mode."""
    return TRADING_MODE == "backtest"


def run_backtest():
    """Run backtest mode."""
    logger.info("=" * 60)
    logger.info("BACKTEST MODE")
    logger.info("=" * 60)
    
    logger.info("Backtest mode not yet implemented - use backtest.py")
    return True


def run_paper_trading():
    """Run paper trading mode."""
    logger.info("=" * 60)
    logger.info("PAPER TRADING MODE")
    logger.info("=" * 60)
    
    strategy = TrendPullbackStrategy()
    
    # Initial setup
    strategy.refresh_universe()
    
    cycle_count = 0
    
    try:
        while True:
            cycle_count += 1
            logger.info(f"\n--- Cycle {cycle_count} ---")
            
            # Run one full strategy cycle
            result = strategy.run_cycle()
            
            logger.info(
                f"Cycle result: {result['exits']} exits, "
                f"{result['entries']} entries, "
                f"{result['open_positions']} open positions"
            )
            
            # Status check every 10 cycles
            if cycle_count % 10 == 0:
                logger.info(f"Completed {cycle_count} cycles")
            
            # Sleep until next cycle
            sleep_seconds = 3600  # 1 hour for paper
            logger.info(f"Sleeping {sleep_seconds}s until next cycle...")
            time.sleep(sleep_seconds)
            
    except KeyboardInterrupt:
        logger.info("Paper trading stopped by user")
        return True


def run_live_trading():
    """Run live trading mode."""
    logger.info("=" * 60)
    logger.info("LIVE TRADING MODE")
    logger.info("=" * 60)
    
    # Extra safety check
    confirm = input("Confirm live trading? Type 'LIVE' to proceed: ")
    if confirm != "LIVE":
        logger.warning("Live trading not confirmed. Exiting.")
        return False
    
    strategy = TrendPullbackStrategy()
    strategy.refresh_universe()
    
    cycle_count = 0
    
    try:
        while True:
            cycle_count += 1
            logger.info(f"\n--- Live Cycle {cycle_count} ---")
            
            result = strategy.run_cycle()
            
            logger.info(
                f"Cycle result: {result['exits']} exits, "
                f"{result['entries']} entries, "
                f"{result['open_positions']} open positions"
            )
            
            # Sleep until next cycle (4H candle close)
            time.sleep(14400)
            
    except KeyboardInterrupt:
        logger.info("Live trading stopped by user")
        return True


def main():
    """Main entry point."""
    print("=" * 60)
    print("CRYPTO PERPETUAL FUTURES BOT")
    print("Trend-Pullback Strategy | 8-Layer Architecture")
    print("=" * 60)
    
    # Parse arguments
    parser = argparse.ArgumentParser(description="Crypto Perpetual Futures Bot")
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "live"],
        default=None,
        help="Override trading mode from .env"
    )
    args = parser.parse_args()
    
    # Determine mode
    mode = args.mode or get_trading_mode()
    print(f"\nTrading mode: {mode.upper()}")
    
    # Validate configuration
    config_result = validate_config()
    if not config_result["valid"]:
        print("\n❌ Configuration errors:")
        for error in config_result["errors"]:
            print(f"  - {error}")
        for warning in config_result["warnings"]:
            print(f"  ⚠️  {warning}")
        sys.exit(1)
    
    if config_result["warnings"]:
        print("\n⚠️  Configuration warnings:")
        for warning in config_result["warnings"]:
            print(f"  - {warning}")
    
    print("\n✅ Configuration valid")
    
    # Run based on mode
    if mode == "backtest":
        success = run_backtest()
    elif mode == "paper":
        success = run_paper_trading()
    elif mode == "live":
        success = run_live_trading()
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
    
    if success:
        print("\n✅ Bot completed successfully")
        sys.exit(0)
    else:
        print("\n❌ Bot encountered errors")
        sys.exit(1)


if __name__ == "__main__":
    main()