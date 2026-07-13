"""
Backtesting runner for the trend-pullback strategy.
Validates strategy on historical data before live deployment.
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

from core.market_data import MarketData
from core.bitget_client import BitgetClient
from strategies.trend_pullback import TrendPullbackStrategy
from data.database import Database
from utils.logger import get_logger
from config.settings import STRATEGY, RISK, PROMOTION

logger = get_logger(__name__)


class BacktestRunner:
    """
    Walk-forward backtesting engine.
    Tests strategy on historical data with realistic costs.
    """
    
    def __init__(self, start_date: datetime, end_date: datetime, symbols: List[str]):
        self.start_date = start_date
        self.end_date = end_date
        self.symbols = symbols
        
        self.market_data = MarketData()
        self.strategy = TrendPullbackStrategy()
        self.db = Database(db_path="data/backtest.db")
        
        # Backtest state
        self.equity = 10000.0  # Starting equity
        self.initial_equity = self.equity
        self.peak_equity = self.equity
        self.trades: List[Dict] = []
        self.daily_equity: Dict[str, float] = {}
        
        # Realistic cost assumptions
        self.maker_fee = 0.0002  # 0.02%
        self.taker_fee = 0.0006  # 0.06%
        self.slippage_bps = 5     # 5 bps average slippage
        
        logger.info(
            f"Backtest initialized: {start_date.date()} to {end_date.date()} | "
            f"Symbols: {len(symbols)} | Equity: ${self.equity}"
        )
    
    def run(self) -> Dict:
        """
        Run the full backtest.
        Fetches all data first, then processes in memory.
        """
        # Pre-fetch all historical data for all symbols
        all_data = {}
        for symbol in self.symbols:
            logger.info(f"Fetching historical data for {symbol}...")
            candles = self._fetch_all_candles(symbol)
            if candles:
                all_data[symbol] = candles
                logger.info(f"  Loaded {len(candles)} candles for {symbol}")
            else:
                logger.warning(f"  No data for {symbol}")
            time.sleep(0.5)  # Rate limit between symbols
        
        if not all_data:
            logger.error("No data fetched for any symbol")
            return {"error": "no_data"}
        
        # Process each symbol
        for symbol, candles in all_data.items():
            self._process_symbol(symbol, candles)
        
        # Calculate results
        results = self._calculate_results()
        
        # Save results
        self._save_results(results)
        
        return results
    
    def _fetch_all_candles(self, symbol: str) -> List[Dict]:
        """
        Fetch all historical candles using Bitget V2 history-candles endpoint.
        Supports startTime/endTime with 90-day max window per request.
        """
        try:
            # Calculate timestamps
            start_ts = int(self.start_date.timestamp() * 1000)
            end_ts = int(self.end_date.timestamp() * 1000)
            total_days = (self.end_date - self.start_date).days
            
            all_candles = []
            chunk_size = 200  # Bitget max for history-candles
            
            # Bitget history-candles has 90-day max window per request
            # If range > 90 days, split into multiple requests
            max_window_ms = 90 * 24 * 60 * 60 * 1000  # 90 days in ms
            
            current_start = start_ts
            current_end = min(start_ts + max_window_ms, end_ts)
            
            while current_start < end_ts:
                params = {
                    "symbol": symbol,
                    "productType": "USDT-FUTURES",
                    "granularity": "4H",
                    "limit": chunk_size,
                    "startTime": str(current_start),
                    "endTime": str(current_end),
                }
                
                candles = self.market_data.client._request(
                    "GET", 
                    "/api/v2/mix/market/history-candles", 
                    params=params
                )
                
                if not candles:
                    break
                
                all_candles.extend(candles)
                
                # Move window forward
                current_start = current_end
                current_end = min(current_start + max_window_ms, end_ts)
                
                time.sleep(0.1)  # Rate limit
            
            # Normalize all candles
            all_candles = self._normalize_candles(all_candles)
            
            # Filter to exact date range (remove any outside)
            filtered = [
                c for c in all_candles
                if start_ts <= c["timestamp"] <= end_ts
            ]
            
            return filtered
            
        except Exception as e:
            logger.error(f"Failed to fetch data for {symbol}: {e}")
            return []
    
    def _normalize_candles(self, raw_candles: List) -> List[Dict]:
        """Convert Bitget candle format to standard OHLCV."""
        candles = []
        for c in raw_candles:
            if isinstance(c, list) and len(c) >= 6:
                candles.append({
                    "timestamp": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                })
            elif isinstance(c, dict):
                candles.append({
                    "timestamp": int(c.get("ts", c.get("timestamp", 0))),
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                    "volume": float(c.get("volume", 0)),
                })
        return candles
    
    def _process_symbol(self, symbol: str, candles: List[Dict]):
        """
        Process a single symbol's candles.
        Walks forward candle by candle.
        """
        if len(candles) < 50:
            logger.warning(f"{symbol}: Insufficient data ({len(candles)} candles)")
            return
        
        # Walk forward with a sliding window
        window_size = 100
        step = 6  # Check every 6 candles (1 day in 4H)
        
        for i in range(window_size, len(candles), step):
            window = candles[i-window_size:i]
            current_candle = candles[i-1]
            
            # Simulate signal detection
            signal = self._simulate_signal(symbol, window)
            
            if signal:
                # Check if we have enough equity
                if self.equity <= 0:
                    break
                
                # Execute trade
                self._execute_trade(signal, current_candle, candles[i:])
                
                # Record daily equity
                day_str = datetime.fromtimestamp(
                    current_candle["timestamp"] / 1000
                ).strftime("%Y-%m-%d")
                self.daily_equity[day_str] = self.equity
    
    def _simulate_signal(self, symbol: str, candles: List[Dict]) -> Optional[Dict]:
        """
        Simulate strategy signal detection on historical candles.
        """
        # Extract data
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        volumes = [c["volume"] for c in candles]
        
        # Layer 1: Regime (ADX)
        from utils.helpers import calculate_adx, calculate_atr
        adx = calculate_adx(highs, lows, closes, STRATEGY.adx_period)
        atr = calculate_atr(highs, lows, closes, STRATEGY.atr_period)
        atr_pct = (atr / closes[-1] * 100) if closes[-1] > 0 else 0
        
        if adx < STRATEGY.adx_threshold or atr_pct < STRATEGY.min_atr_multiplier:
            return None
        
        # Layer 2: Trend (EMAs)
        from utils.helpers import calculate_ema, is_trending_up, is_trending_down
        
        ema_fast = calculate_ema(closes, STRATEGY.ema_fast_4h)
        ema_slow = calculate_ema(closes, STRATEGY.ema_slow_4h)
        
        is_up = is_trending_up(ema_fast, ema_slow)
        is_down = is_trending_down(ema_fast, ema_slow)
        
        if not is_up and not is_down:
            return None
        
        direction = "long" if is_up else "short"
        
        # Layer 3: Structure (simplified)
        from utils.helpers import find_swing_lows, find_swing_highs
        
        if direction == "long":
            swings = find_swing_lows(lows, lookback=3)
            if len(swings) < 2 or swings[-1][1] <= swings[-2][1]:
                return None  # No higher low
        else:
            swings = find_swing_highs(highs, lookback=3)
            if len(swings) < 2 or swings[-1][1] >= swings[-2][1]:
                return None  # No lower high
        
        # Layer 4: Volume (simplified)
        avg_vol = np.mean(volumes[-STRATEGY.volume_lookback:])
        current_vol = volumes[-1]
        if current_vol < avg_vol * STRATEGY.volume_expansion_ratio:
            return None
        
        # Calculate position sizing
        entry_price = closes[-1]
        stop_distance = atr * RISK.atr_stop_multiplier
        stop_distance = max(stop_distance, entry_price * RISK.min_stop_distance_pct)
        stop_distance = min(stop_distance, entry_price * RISK.max_stop_distance_pct)
        
        if direction == "long":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + (stop_distance * 2)
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - (stop_distance * 2)
        
        risk_amount = self.equity * RISK.max_risk_per_trade
        position_value = risk_amount / (stop_distance / entry_price)
        
        return {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "position_value": position_value,
            "stop_distance": stop_distance,
            "risk_amount": risk_amount,
        }
    
    def _execute_trade(self, signal: Dict, entry_candle: Dict, future_candles: List[Dict]):
        """
        Simulate trade execution with realistic costs.
        Forward-walks to see if SL or TP hit first.
        """
        entry = signal["entry_price"]
        sl = signal["stop_loss"]
        tp = signal["take_profit"]
        direction = signal["direction"]
        
        # Apply slippage to entry
        slip_pct = self.slippage_bps / 10000
        if direction == "long":
            entry_executed = entry * (1 + slip_pct)
        else:
            entry_executed = entry * (1 - slip_pct)
        
        # Fee on entry
        entry_fee = signal["position_value"] * self.taker_fee
        
        # Forward-walk: check if SL or TP hit in future candles
        exit_price = None
        exit_reason = None
        
        for candle in future_candles:
            if direction == "long":
                # Check if low hit SL
                if candle["low"] <= sl:
                    exit_price = sl
                    exit_reason = "stop_loss"
                    break
                # Check if high hit TP
                if candle["high"] >= tp:
                    exit_price = tp
                    exit_reason = "take_profit"
                    break
            else:  # short
                # Check if high hit SL
                if candle["high"] >= sl:
                    exit_price = sl
                    exit_reason = "stop_loss"
                    break
                # Check if low hit TP
                if candle["low"] <= tp:
                    exit_price = tp
                    exit_reason = "take_profit"
                    break
        
        # If neither hit within lookahead, close at last candle
        if exit_price is None and future_candles:
            exit_price = future_candles[-1]["close"]
            exit_reason = "time_exit"
        elif exit_price is None:
            return  # No future data, skip
        
        # Apply slippage to exit
        if direction == "long":
            exit_executed = exit_price * (1 - slip_pct)
        else:
            exit_executed = exit_price * (1 + slip_pct)
        
        # Calculate P&L
        if direction == "long":
            pnl = (exit_executed - entry_executed) * (signal["position_value"] / entry_executed)
        else:
            pnl = (entry_executed - exit_executed) * (signal["position_value"] / entry_executed)
        
        # Fee on exit
        exit_fee = signal["position_value"] * self.taker_fee
        
        # Net P&L
        net_pnl = pnl - entry_fee - exit_fee
        
        # Update equity
        self.equity += net_pnl
        
        # Record trade
        trade = {
            "symbol": signal["symbol"],
            "direction": direction,
            "entry": entry_executed,
            "exit": exit_executed,
            "sl": sl,
            "tp": tp,
            "pnl": net_pnl,
            "pnl_pct": net_pnl / self.initial_equity,
            "r_multiple": net_pnl / signal["risk_amount"] if signal["risk_amount"] > 0 else 0,
            "fees": entry_fee + exit_fee,
            "exit_reason": exit_reason,
        }
        self.trades.append(trade)
        
        logger.info(
            f"Trade: {signal['symbol']} {direction} | "
            f"Entry: {entry_executed:.2f} | Exit: {exit_executed:.2f} | "
            f"Reason: {exit_reason} | PnL: ${net_pnl:+.2f} | Equity: ${self.equity:.2f}"
        )
    
    def _calculate_results(self) -> Dict:
        """Calculate comprehensive backtest metrics."""
        if not self.trades:
            return {"error": "No trades generated"}
        
        total_trades = len(self.trades)
        winners = [t for t in self.trades if t["pnl"] > 0]
        losers = [t for t in self.trades if t["pnl"] <= 0]
        
        gross_profit = sum(t["pnl"] for t in winners)
        gross_loss = sum(t["pnl"] for t in losers)
        net_pnl = gross_profit + gross_loss
        
        total_fees = sum(t["fees"] for t in self.trades)
        
        # Drawdown
        peak = self.initial_equity
        max_dd = 0
        running = self.initial_equity
        
        for trade in self.trades:
            running += trade["pnl"]
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd
        
        max_dd_pct = max_dd / peak if peak > 0 else 0
        
        # Returns
        returns = [t["pnl_pct"] for t in self.trades]
        avg_return = np.mean(returns) if returns else 0
        std_return = np.std(returns) if returns else 0
        
        # Sharpe (simplified, assuming risk-free rate = 0)
        sharpe = (avg_return / std_return * np.sqrt(252)) if std_return > 0 else 0
        
        # Profit factor
        pf = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')
        
        # Expectancy
        win_rate = len(winners) / total_trades if total_trades > 0 else 0
        avg_winner = gross_profit / len(winners) if winners else 0
        avg_loser = gross_loss / len(losers) if losers else 0
        expectancy = (win_rate * avg_winner) + ((1 - win_rate) * avg_loser)
        
        return {
            "total_trades": total_trades,
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": win_rate,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "net_pnl": net_pnl,
            "total_fees": total_fees,
            "return_pct": (self.equity - self.initial_equity) / self.initial_equity,
            "max_drawdown": max_dd,
            "max_drawdown_pct": max_dd_pct,
            "profit_factor": pf,
            "sharpe_ratio": sharpe,
            "expectancy": expectancy,
            "avg_winner": avg_winner,
            "avg_loser": avg_loser,
            "avg_r": np.mean([t["r_multiple"] for t in self.trades]),
            "final_equity": self.equity,
            "fees_included": True,
            "slippage_included": True,
        }
    
    def _save_results(self, results: Dict):
        """Save backtest results to file."""
        os.makedirs("backtest_results", exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backtest_results/backtest_{timestamp}.json"
        
        output = {
            "config": {
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "symbols": self.symbols,
                "initial_equity": self.initial_equity,
                "maker_fee": self.maker_fee,
                "taker_fee": self.taker_fee,
                "slippage_bps": self.slippage_bps,
            },
            "results": results,
            "trades": self.trades,
        }
        
        with open(filename, "w") as f:
            json.dump(output, f, indent=2, default=str)
        
        logger.info(f"Backtest results saved: {filename}")
        
        # Print summary
        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)
        
        if "error" in results:
            print(f"Status: {results['error']}")
            print(f"No trades generated in this period.")
            print("=" * 60)
            return
        
        print(f"Total Trades: {results['total_trades']}")
        print(f"Win Rate: {results['win_rate']*100:.1f}%")
        print(f"Net P&L: ${results['net_pnl']:+.2f} ({results['return_pct']*100:+.2f}%)")
        print(f"Max Drawdown: {results['max_drawdown_pct']*100:.2f}%")
        print(f"Profit Factor: {results['profit_factor']:.2f}")
        print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
        print(f"Expectancy: ${results['expectancy']:.2f}")
        print(f"Total Fees: ${results['total_fees']:.2f}")
        print("=" * 60)


def main():
    """Run backtest from command line."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Backtest the strategy")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    args = parser.parse_args()
    
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    
    runner = BacktestRunner(start, end, args.symbols)
    results = runner.run()
    
    return results


if __name__ == "__main__":
    main()