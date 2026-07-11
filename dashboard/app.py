"""
Trade With Dezify - Flask Dashboard
SEPARATE PROCESS VERSION - Bot runs outside Gunicorn workers
"""

import os
import sys
import subprocess
import signal
import json
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template, jsonify, request, session

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "dezify_secret_key_2026"

PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "Adebayo")

# =============================================================================
# REAL BOT IMPORTS (for status queries only)
# =============================================================================
try:
    from config.settings import BITGET, RISK, BACKTEST
    from data.database import Database
    from core.bitget_client import BitgetClient
    BOT_AVAILABLE = True
    print("✅ Bot modules loaded successfully")
except Exception as e:
    print(f"❌ Bot modules not available: {e}")
    BOT_AVAILABLE = False

try:
    from backtest.engine import BacktestEngine
    BACKTEST_AVAILABLE = True
    print("✅ Backtest engine loaded")
except Exception as e:
    print(f"⚠️ Backtest engine not available: {e}")
    BACKTEST_AVAILABLE = False
    BacktestEngine = None  # type: ignore


# =============================================================================
# SINGLE SOURCE OF TRUTH: PID FILE
# =============================================================================
PID_FILE = Path("data/bot.pid")
BOT_LOG_FILE = Path("data/bot.log")


def _read_pid_file() -> Optional[int]:
    """Read PID from file."""
    try:
        if PID_FILE.exists():
            with open(PID_FILE, "r") as f:
                data = json.load(f)
                return data.get("pid")
    except Exception:
        pass
    return None


def _write_pid_file(pid: int, mode: str = "paper"):
    """Write PID and metadata to file."""
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PID_FILE, "w") as f:
            json.dump({
                "pid": pid,
                "mode": mode,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }, f)
    except Exception as e:
        print(f"Failed to write PID file: {e}")


def _delete_pid_file():
    """Remove PID file."""
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


def _is_pid_alive(pid: Optional[int]) -> bool:
    """Check if a process with this PID actually exists."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_bot_running() -> bool:
    """Single source of truth - works across ALL devices and workers."""
    pid = _read_pid_file()
    if pid is None:
        return False
    if _is_pid_alive(pid):
        return True
    _delete_pid_file()
    return False


def _get_bot_info() -> Dict:
    """Get bot info from PID file."""
    try:
        if PID_FILE.exists():
            with open(PID_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _read_bot_log(last_n: int = 50) -> List[str]:
    """Read last N lines from bot log file."""
    try:
        if BOT_LOG_FILE.exists():
            with open(BOT_LOG_FILE, "r") as f:
                lines = f.readlines()
                return lines[-last_n:]
    except Exception:
        pass
    return []


# =============================================================================
# DEMO DATA
# =============================================================================
DEMO_POSITIONS = {
    "paper": [
        {
            "id": "demo-p1",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": 98500.0,
            "current_price": 101200.0,
            "stop_loss": 96000.0,
            "take_profit": 108000.0,
            "position_size": 0.05,
            "position_value": 5060.0,
            "leverage": 5,
            "risk_pct": 0.01,
            "pnl_pct": 2.74,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "r_multiple": 0.55,
        },
        {
            "id": "demo-p2", 
            "symbol": "ETHUSDT",
            "direction": "short",
            "entry_price": 3650.0,
            "current_price": 3520.0,
            "stop_loss": 3800.0,
            "take_profit": 3200.0,
            "position_size": 0.8,
            "position_value": 2816.0,
            "leverage": 3,
            "risk_pct": 0.015,
            "pnl_pct": 3.56,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "r_multiple": 0.71,
        },
    ],
    "backtest": [
        {
            "id": "demo-bt1",
            "symbol": "SOLUSDT",
            "direction": "long",
            "entry_price": 145.0,
            "current_price": 162.0,
            "stop_loss": 130.0,
            "take_profit": 180.0,
            "position_size": 12.0,
            "position_value": 1944.0,
            "leverage": 4,
            "risk_pct": 0.012,
            "pnl_pct": 11.72,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "r_multiple": 1.13,
        },
    ],
    "live": [],
}

DEMO_TRADES = {
    "paper": [
        {
            "trade_id": "demo-t1",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": 94200.0,
            "exit_price": 97800.0,
            "pnl": 180.5,
            "pnl_pct": 3.82,
            "r_multiple": 1.95,
            "entry_time": "2026-07-08T14:30:00",
            "exit_time": "2026-07-09T09:15:00",
            "regime": "trending",
            "exit_reason": "take_profit",
        },
        {
            "trade_id": "demo-t2",
            "symbol": "ETHUSDT",
            "direction": "short",
            "entry_price": 3820.0,
            "exit_price": 3650.0,
            "pnl": 136.0,
            "pnl_pct": 4.45,
            "r_multiple": 2.12,
            "entry_time": "2026-07-09T11:00:00",
            "exit_time": "2026-07-10T16:45:00",
            "regime": "trending",
            "exit_reason": "take_profit",
        },
        {
            "trade_id": "demo-t3",
            "symbol": "SOLUSDT",
            "direction": "long",
            "entry_price": 138.0,
            "exit_price": 132.0,
            "pnl": -72.0,
            "pnl_pct": -4.35,
            "r_multiple": -1.0,
            "entry_time": "2026-07-10T08:20:00",
            "exit_time": "2026-07-10T22:10:00",
            "regime": "ranging",
            "exit_reason": "stop_loss",
        },
    ],
    "backtest": [
        {
            "trade_id": "demo-bt1",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": 65000.0,
            "exit_price": 72000.0,
            "pnl": 525.0,
            "pnl_pct": 10.77,
            "r_multiple": 3.5,
            "entry_time": "2024-03-01T10:00:00",
            "exit_time": "2024-03-15T14:00:00",
            "regime": "trending",
            "exit_reason": "take_profit",
        },
        {
            "trade_id": "demo-bt2",
            "symbol": "ETHUSDT",
            "direction": "short",
            "entry_price": 3500.0,
            "exit_price": 3100.0,
            "pnl": 320.0,
            "pnl_pct": 11.43,
            "r_multiple": 2.86,
            "entry_time": "2024-04-10T09:00:00",
            "exit_time": "2024-04-25T16:00:00",
            "regime": "trending",
            "exit_reason": "take_profit",
        },
    ],
    "live": [],
}

DEMO_STATS = {
    "paper": {"equity": 12500.0, "open_positions": 2, "today_pnl": 245.0, "today_trades": 3, "win_rate": 68.5, "avg_r": 1.42, "total_risk": 2.5},
    "backtest": {"equity": 18750.0, "open_positions": 1, "today_pnl": 0.0, "today_trades": 0, "win_rate": 72.0, "avg_r": 1.85, "total_risk": 1.2},
    "live": {"equity": 50000.0, "open_positions": 0, "today_pnl": 0.0, "today_trades": 0, "win_rate": 0.0, "avg_r": 0.0, "total_risk": 0.0},
}


def _is_demo_mode() -> bool:
    return request.args.get("demo", "0") == "1" or request.args.get("demo_mode", "0") == "1"


# =============================================================================
# CACHES (per-worker, refreshed from DB)
# =============================================================================
_positions_cache = []
_trades_cache = []
_stats_cache = {
    "equity": 10000.0,
    "open_positions": 0,
    "today_pnl": 0.0,
    "today_trades": 0,
    "win_rate": 0.0,
    "avg_r": 0.0,
    "total_risk": 0.0,
}
_scan_log = []
_mode = "paper"


def _update_from_database():
    global _trades_cache, _stats_cache
    try:
        db = Database()
        all_trades = db.get_all_trades(limit=100)
        _trades_cache = []
        for t in all_trades:
            _trades_cache.append({
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": round(t.realized_pnl, 2),
                "pnl_pct": round(t.realized_pnl_pct * 100, 2),
                "r_multiple": round(t.r_multiple, 2),
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "regime": t.market_regime,
                "exit_reason": t.checklist.exit_reason if t.checklist else "",
            })
        closed = [t for t in all_trades if t.is_closed()]
        today = datetime.now(timezone.utc).date()
        today_trades = [t for t in closed if t.exit_time and t.exit_time.date() == today]
        total_pnl = sum(t.realized_pnl for t in today_trades)
        winners = sum(1 for t in closed if t.is_winner())
        total_closed = len(closed)
        win_rate = (winners / total_closed * 100) if total_closed > 0 else 0
        avg_r = sum(t.r_multiple for t in closed) / len(closed) if closed else 0
        latest_perf = db.get_daily_performance(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        equity = latest_perf.ending_equity if latest_perf else (BACKTEST.initial_equity if hasattr(BACKTEST, "initial_equity") else 10000.0)
        _stats_cache.update({
            "equity": round(equity, 2),
            "today_pnl": round(total_pnl, 2),
            "today_trades": len(today_trades),
            "win_rate": round(win_rate, 1),
            "avg_r": round(avg_r, 2),
        })
    except Exception as e:
        print(f"Database update error: {e}")


# =============================================================================
# FLASK ROUTES
# =============================================================================

@app.route("/")
def home():
    if session.get("logged_in"):
        return render_template("dashboard.html")
    return render_template("login.html")


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    if data and data.get("password") == PASSWORD:
        session["logged_in"] = True
        return jsonify({"success": True})
    return jsonify({"success": False}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.pop("logged_in", None)
    return jsonify({"success": True})


@app.route("/api/mode", methods=["POST"])
def set_mode():
    global _mode
    data = request.get_json()
    _mode = data.get("mode", "paper")
    return jsonify({"success": True, "mode": _mode})


@app.route("/api/start", methods=["POST"])
def start_bot():
    global _mode

    # === SINGLE SOURCE OF TRUTH ===
    if _is_bot_running():
        info = _get_bot_info()
        return jsonify({
            "success": False,
            "error": "Bot already running",
            "message": f"Bot is already running (PID {info.get('pid')}, started {info.get('started_at', 'unknown')})",
            "already_running": True,
            "pid": info.get("pid"),
            "started_at": info.get("started_at"),
        }), 409

    data = request.get_json() or {}
    _mode = data.get("mode", _mode)

    if _mode == "backtest":
        return jsonify({"success": False, "error": "Backtest mode not yet implemented in separate process."}), 503

    elif _mode == "live":
        return jsonify({"success": False, "error": "Live mode not available through dashboard."}), 403

    else:  # paper
        if not BOT_AVAILABLE:
            return jsonify({"success": False, "error": "Bot modules not available."}), 500

        # === START BOT AS SEPARATE PROCESS ===
        # This is the key fix: bot runs in its OWN process, not inside Gunicorn
        try:
            # Create a temporary script to run the bot
            bot_script = Path("data/run_bot.py")
            bot_script.parent.mkdir(parents=True, exist_ok=True)

            script_content = """
import sys
import os
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.trend_pullback import TrendPullbackStrategy

# Redirect output to log file
log_file = Path("data/bot.log")
log_file.parent.mkdir(parents=True, exist_ok=True)

class Logger:
    def __init__(self, filepath):
        self.file = open(filepath, "a")
        self.stdout = sys.stdout
        sys.stdout = self
        sys.stderr = self

    def write(self, message):
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if message.strip():
            self.file.write(f"[{timestamp}] {message}")
            self.file.flush()
        self.stdout.write(message)

    def flush(self):
        self.file.flush()
        self.stdout.flush()

logger = Logger(log_file)

print("=" * 50)
print("BOT PROCESS STARTED")
print("=" * 50)

try:
    strategy = TrendPullbackStrategy()
    print("Strategy created")

    strategy.refresh_universe()
    print(f"Universe: {len(strategy.universe)} symbols")

    cycle = 0
    while True:
        cycle += 1
        print(f"Cycle {cycle}...")
        result = strategy.run_cycle()
        print(f"Cycle {cycle} complete: {result}")
        time.sleep(60)

except Exception as e:
    print(f"BOT CRASHED: {e}")
    traceback.print_exc()
    raise
"""

            with open(bot_script, "w") as f:
                f.write(script_content)

            # Start the bot process
            process = subprocess.Popen(
                [sys.executable, str(bot_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(Path(__file__).parent.parent),
            )

            # Write PID file
            _write_pid_file(process.pid, "paper")

            # Wait a moment and verify
            time.sleep(2)
            if not _is_pid_alive(process.pid):
                _delete_pid_file()
                logs = _read_bot_log(20)
                log_text = "\n".join(logs) if logs else "No log output"
                return jsonify({
                    "success": False,
                    "error": "Bot process failed to start",
                    "logs": log_text,
                }), 500

            return jsonify({
                "success": True,
                "message": "Paper trading started",
                "mode": "paper",
                "pid": process.pid,
            })

        except Exception as e:
            _delete_pid_file()
            return jsonify({"success": False, "error": f"Failed to start bot: {str(e)}"}), 500


@app.route("/api/stop", methods=["POST"])
def stop_bot():
    """Stop the bot process."""
    pid = _read_pid_file()
    if pid and _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if _is_pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    _delete_pid_file()
    return jsonify({"success": True, "message": "Bot stopped"})


@app.route("/api/force-restart", methods=["POST"])
def force_restart():
    """Force kill bot and reset."""
    pid = _read_pid_file()
    if pid and _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(1)
        except Exception:
            pass
    _delete_pid_file()
    if BOT_LOG_FILE.exists():
        try:
            BOT_LOG_FILE.unlink()
        except Exception:
            pass
    return jsonify({"success": True, "message": "State reset. You can now click Start Bot."})


@app.route("/api/status")
def get_status():
    """Get status - consistent across ALL devices."""

    if _is_demo_mode():
        mode = request.args.get("mode", _mode)
        stats = DEMO_STATS.get(mode, DEMO_STATS["paper"])
        return jsonify({
            "running": False, "mode": mode, "cycle_count": 0,
            "last_cycle_time": None, "last_error": None,
            **stats, "demo": True,
            "bot_available": BOT_AVAILABLE, "backtest_available": BACKTEST_AVAILABLE,
        })

    is_running = _is_bot_running()
    info = _get_bot_info()

    _update_from_database()

    # Get recent log lines if running
    recent_logs = []
    if is_running:
        recent_logs = _read_bot_log(10)

    return jsonify({
        "running": is_running,
        "mode": info.get("mode", _mode),
        "cycle_count": 0,  # Would need IPC to get from bot process
        "last_cycle_time": None,
        "last_error": None,
        "equity": _stats_cache["equity"],
        "open_positions": _stats_cache["open_positions"],
        "today_pnl": _stats_cache["today_pnl"],
        "today_trades": _stats_cache["today_trades"],
        "win_rate": _stats_cache["win_rate"],
        "avg_r": _stats_cache["avg_r"],
        "total_risk": _stats_cache["total_risk"],
        "demo": False,
        "bot_available": BOT_AVAILABLE,
        "backtest_available": BACKTEST_AVAILABLE,
        "pid": info.get("pid"),
        "started_at": info.get("started_at"),
        "recent_logs": recent_logs,
    })


@app.route("/api/positions")
def get_positions():
    if _is_demo_mode():
        mode = request.args.get("mode", _mode)
        return jsonify({"positions": DEMO_POSITIONS.get(mode, []), "count": len(DEMO_POSITIONS.get(mode, [])), "mode": mode, "demo": True})
    _update_from_database()
    # Get open trades from DB
    try:
        db = Database()
        open_trades = db.get_open_trades()
        positions = []
        for t in open_trades:
            positions.append({
                "id": t.trade_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "current_price": t.entry_price,  # Would need live price
                "stop_loss": t.stop_loss_price,
                "take_profit": t.take_profit_price,
                "position_size": t.position_size,
                "position_value": t.position_value_usd,
                "leverage": t.leverage,
                "risk_pct": t.risk_pct,
                "pnl_pct": 0,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "r_multiple": 0,
            })
        return jsonify({"positions": positions, "count": len(positions), "mode": _mode, "demo": False})
    except Exception as e:
        return jsonify({"positions": [], "count": 0, "mode": _mode, "demo": False, "error": str(e)})


@app.route("/api/trades")
def get_trades():
    if _is_demo_mode():
        mode = request.args.get("mode", _mode)
        return jsonify({"trades": DEMO_TRADES.get(mode, []), "total": len(DEMO_TRADES.get(mode, [])), "mode": mode, "demo": True})
    _update_from_database()
    return jsonify({"trades": _trades_cache, "total": len(_trades_cache), "mode": _mode, "demo": False})


@app.route("/api/trade/<trade_id>")
def get_trade_detail(trade_id):
    try:
        db = Database()
        trade = db.get_trade(trade_id)
        if not trade:
            return jsonify({"error": "Trade not found"}), 404
        checklist = trade.checklist.to_dict() if trade.checklist else {}
        return jsonify({
            "trade_id": trade.trade_id, "symbol": trade.symbol, "direction": trade.direction,
            "layers": {
                "layer_1": {"name": "Market Regime", "passed": checklist.get("adx_above_threshold", False), "adx": checklist.get("adx_value", 0), "regime": trade.market_regime, "explanation": f"ADX: {checklist.get('adx_value', 0):.1f}. Regime: {trade.market_regime}."},
                "layer_2": {"name": "Trend Analysis", "passed": checklist.get("daily_ema_aligned", False) or checklist.get("fourh_ema_aligned", False), "score": checklist.get("trend_score", 0), "explanation": f"Daily: {checklist.get('daily_ema_aligned', False)}. 4H: {checklist.get('fourh_ema_aligned', False)}."},
                "layer_3": {"name": "Pullback Detection", "passed": checklist.get("pullback_confirmed", False), "depth": checklist.get("pullback_depth", 0), "explanation": f"Depth: {checklist.get('pullback_depth', 0):.4f}"},
                "layer_4": {"name": "Entry Trigger", "passed": checklist.get("entry_triggered", False), "entry_price": checklist.get("entry_price"), "stop_loss": checklist.get("stop_loss_price"), "take_profit": checklist.get("take_profit_price"), "explanation": f"Triggered: {checklist.get('entry_triggered', False)}"},
                "layer_5": {"name": "Risk Management", "passed": True, "risk_pct": checklist.get("risk_pct", 0), "leverage": checklist.get("leverage", 1), "position_size": checklist.get("position_size", 0), "explanation": f"Risk: {checklist.get('risk_pct', 0)*100:.2f}%"},
                "layer_6": {"name": "Execution", "passed": True, "explanation": "Order executed."},
                "layer_7": {"name": "Trade Management", "passed": True, "exit_reason": checklist.get("exit_reason", "still_open"), "explanation": f"Exit: {checklist.get('exit_reason', 'still_open')}"},
                "layer_8": {"name": "Performance Review", "passed": True, "realized_pnl": trade.realized_pnl, "r_multiple": trade.r_multiple, "explanation": f"PnL: ${trade.realized_pnl:.2f}. R: {trade.r_multiple:.2f}R"},
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/balance")
def get_balance():
    mode = request.args.get("mode", _mode)
    if _is_demo_mode():
        stats = DEMO_STATS.get(mode, DEMO_STATS["paper"])
        return jsonify({"mode": mode, "balance": stats["equity"], "source": "demo", "currency": "USDT", "demo": True})
    if mode == "live":
        try:
            client = BitgetClient()
            balance = client.get_account_equity()
            return jsonify({"mode": "live", "balance": balance, "source": "bitget_api", "currency": "USDT", "demo": False})
        except Exception as e:
            return jsonify({"mode": "live", "balance": 0, "error": str(e), "source": "error", "demo": False})
    elif mode == "paper":
        _update_from_database()
        return jsonify({"mode": "paper", "balance": _stats_cache["equity"], "source": "paper_account", "currency": "USDT", "demo": False})
    else:
        equity = BACKTEST.initial_equity if hasattr(BACKTEST, "initial_equity") else 10000.0
        return jsonify({"mode": "backtest", "balance": equity, "source": "backtest_config", "currency": "USDT", "demo": False})


@app.route("/api/bot-logs")
def get_bot_logs():
    """Get recent bot log lines."""
    lines = _read_bot_log(100)
    return jsonify({"logs": lines, "running": _is_bot_running()})


@app.route("/api/performance-report")
def performance_report():
    try:
        from core.performance_logger import get_performance_logger
        import io
        logger = get_performance_logger()
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        logger.report()
        sys.stdout = old_stdout
        return jsonify({"success": True, "report": buffer.getvalue()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    print("=" * 60)
    print("TRADE WITH DEZIFY - Separate Process Dashboard")
    print("=" * 60)
    print(f"Bot modules available: {BOT_AVAILABLE}")
    print(f"Backtest engine available: {BACKTEST_AVAILABLE}")
    print("Dashboard: http://127.0.0.1:5000")
    print("Password: Adebayo")
    print("=" * 60)
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)