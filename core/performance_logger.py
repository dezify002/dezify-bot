"""
Performance Logger - Tracks ADX vs time-to-resolution
Add to your existing Analytics class or call from trade exit
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class TradeResolution:
    trade_id: str
    symbol: str
    direction: str
    adx_at_entry: float
    regime_bucket: str  # strong_trend, moderate_trend, weak_trend
    entry_time: datetime
    exit_time: Optional[datetime]
    exit_reason: str  # take_profit, stop_loss, time_exit, manual
    time_in_trade_hours: float
    r_multiple: float
    realized_pnl: float


class PerformanceLogger:
    """
    Logs every trade's ADX and time-to-resolution.
    Run report after 20+ trades to see patterns.
    """

    LOG_FILE = Path("data/trade_resolutions.json")

    def __init__(self):
        self.resolutions: List[TradeResolution] = []
        self._load()

    def _load(self):
        if self.LOG_FILE.exists():
            with open(self.LOG_FILE) as f:
                data = json.load(f)
                for item in data:
                    item["entry_time"] = datetime.fromisoformat(item["entry_time"])
                    item["exit_time"] = datetime.fromisoformat(item["exit_time"]) if item["exit_time"] else None
                    self.resolutions.append(TradeResolution(**item))

    def _save(self):
        self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for r in self.resolutions:
            d = asdict(r)
            d["entry_time"] = r.entry_time.isoformat()
            d["exit_time"] = r.exit_time.isoformat() if r.exit_time else None
            data.append(d)
        with open(self.LOG_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def log_trade(self, trade, checklist):
        """
        Call this when a trade exits.

        Args:
            trade: TradeRecord object
            checklist: TradeChecklist object (from entry)
        """
        adx = checklist.adx_value if checklist else 0

        # Bucket by regime strength
        if adx >= 35:
            bucket = "strong_trend"
        elif adx >= 30:
            bucket = "moderate_trend"
        else:
            bucket = "weak_trend"

        # Calculate time in trade
        hours = 0
        if trade.exit_time and trade.entry_time:
            hours = (trade.exit_time - trade.entry_time).total_seconds() / 3600

        resolution = TradeResolution(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            direction=trade.direction,
            adx_at_entry=adx,
            regime_bucket=bucket,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            exit_reason=trade.checklist.exit_reason if trade.checklist else "unknown",
            time_in_trade_hours=round(hours, 2),
            r_multiple=round(trade.r_multiple, 2),
            realized_pnl=round(trade.realized_pnl, 2),
        )

        self.resolutions.append(resolution)
        self._save()
        print(f"📊 Logged: {trade.symbol} | ADX: {adx:.1f} ({bucket}) | Time: {hours:.1f}h | R: {trade.r_multiple:.2f}")

    def report(self):
        """
        Print report after you have 20+ trades.
        Call this from command line or dashboard.
        """
        if len(self.resolutions) < 10:
            print(f"Need 20+ trades for meaningful report. Currently: {len(self.resolutions)}")
            return

        print("\n" + "=" * 60)
        print("TRADE RESOLUTION REPORT")
        print("=" * 60)

        # Group by regime bucket
        buckets = {}
        for r in self.resolutions:
            if r.regime_bucket not in buckets:
                buckets[r.regime_bucket] = []
            buckets[r.regime_bucket].append(r)

        for bucket, trades in sorted(buckets.items()):
            avg_time = sum(t.time_in_trade_hours for t in trades) / len(trades)
            avg_r = sum(t.r_multiple for t in trades) / len(trades)
            win_rate = sum(1 for t in trades if t.r_multiple > 0) / len(trades) * 100

            print(f"\n{bucket.upper().replace('_', ' ')} (ADX threshold: {self._adx_threshold(bucket)})")
            print(f"  Trades: {len(trades)}")
            print(f"  Avg time to resolve: {avg_time:.1f} hours")
            print(f"  Win rate: {win_rate:.1f}%")
            print(f"  Avg R: {avg_r:.2f}")

        # Overall
        all_times = [t.time_in_trade_hours for t in self.resolutions]
        print(f"\nOVERALL")
        print(f"  Total trades: {len(self.resolutions)}")
        print(f"  Avg time: {sum(all_times)/len(all_times):.1f}h")
        print(f"  Fastest: {min(all_times):.1f}h | Slowest: {max(all_times):.1f}h")

        # Recommendation
        weak = buckets.get("weak_trend", [])
        strong = buckets.get("strong_trend", [])
        if weak and strong:
            weak_avg = sum(t.time_in_trade_hours for t in weak) / len(weak)
            strong_avg = sum(t.time_in_trade_hours for t in strong) / len(strong)
            if weak_avg > strong_avg * 2:
                print(f"\n⚠️ RECOMMENDATION: Weak-trend trades take {weak_avg/strong_avg:.1f}x longer.")
                print(f"   Consider raising ADX threshold from 25 to 30.")

        print("=" * 60)

    def _adx_threshold(self, bucket):
        return {"strong_trend": "35+", "moderate_trend": "30-35", "weak_trend": "25-30"}.get(bucket, "?")


# Singleton instance
_logger = None

def get_performance_logger():
    global _logger
    if _logger is None:
        _logger = PerformanceLogger()
    return _logger


# Convenience function - call from your trade exit code
def log_trade_resolution(trade, checklist):
    get_performance_logger().log_trade(trade, checklist)