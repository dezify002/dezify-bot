"""
Trade With Dezify - Flask Dashboard
DIAGNOSTIC v3: PIPE capture, in-memory logs, file listing debug
"""

import os
import sys
import subprocess
import signal
import json
import time
import traceback
import threading
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
# SINGLE SOURCE OF TRUTH: PID FILE + IN-MEMORY LOGS
# =============================================================================
PID_FILE = Path("data/bot.pid")

# In-memory log storage (shared across workers via file, but also kept in memory)
_bot_logs = []  # List of {"time": str, "msg": str, "type": "log"|"err"}
_bot_log_lock = threading.Lock()
_bot_process = None  # Reference to running subprocess


def _add_bot_log(msg: str, log_type: str = "log"):
    """Add a log entry to in-memory storage."""
    with _bot_log_lock:
        _bot_logs.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "msg": msg,
            "type": log_type,
        })
        # Keep only last 500 entries
        if len(_bot_logs) > 500:
            _bot_logs.pop(0)


def _get_bot_logs(last_n: int = 100) -> List[Dict]:
    """Get last N log entries."""
    with _bot_log_lock:
        return _bot_logs[-last_n:] if len(_bot_logs) > last_n else _bot_logs.copy()


def _clear_bot_logs():
    """Clear in-memory logs."""
    global _bot_logs
    with _bot_log_lock:
        _bot_logs = []


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
    if pid and _is_pid_alive(pid):
        return True

    # FALLBACK: Check for open positions in database
    try:
        db = Database()
        open_trades = db.get_open_trades()
        if open_trades and len(open_trades) > 0:
            return True
    except Exception:
        pass

    if pid:
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


def _list_data_files() -> List[str]:
    """List all files in data/ directory for debugging."""
    try:
        data_dir = Path("data")
        if data_dir.exists():
            return [str(f.relative_to(data_dir)) for f in data_dir.rglob("*") if f.is_file()]
    except Exception as e:
        return [f"Error: {e}"]
    return []


def _check_strategy_file() -> Dict[str, Any]:
    """Check if strategy files exist and are readable."""
    base_dir = Path(__file__).parent.parent
    v3_file = base_dir / "strategies" / "trend_pullback_v3.py"
    v2_file = base_dir / "strategies" / "trend_pullback.py"

    result = {
        "v3_exists": v3_file.exists(),
        "v3_size": v3_file.stat().st_size if v3_file.exists() else 0,
        "v2_exists": v2_file.exists(),
        "v2_size": v2_file.stat().st_size if v2_file.exists() else 0,
        "base_dir": str(base_dir),
        "cwd": os.getcwd(),
        "data_files": _list_data_files(),
    }
    return result


def _read_subprocess_output(process: subprocess.Popen):
    """Read subprocess stdout/stderr in a background thread."""
    def reader():
        try:
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    if line:
                        _add_bot_log(line.rstrip(), "log")
            if process.stderr:
                for line in iter(process.stderr.readline, ''):
                    if line:
                        _add_bot_log(line.rstrip(), "err")
        except Exception as e:
            _add_bot_log(f"Log reader error: {e}", "err")

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return thread


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
    global _mode, _bot_process

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
        try:
            # Clear old logs
            _clear_bot_logs()

            # Check strategy file exists
            strategy_check = _check_strategy_file()
            if not strategy_check["v3_exists"]:
                return jsonify({
                    "success": False,
                    "error": "Strategy file not found",
                    "strategy_check": strategy_check,
                }), 500

            # Create bot script that prints to stdout (captured via PIPE)
            bot_script = Path("data/run_bot.py")
            bot_script.parent.mkdir(parents=True, exist_ok=True)

            script_content = r"""
import sys
import os
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone

# IMMEDIATE OUTPUT - before any imports
print("[BOT] === PROCESS STARTED ===", flush=True)
print(f"[BOT] Python: {sys.executable}", flush=True)
print(f"[BOT] CWD: {os.getcwd()}", flush=True)

base_dir = Path(__file__).parent.parent
print(f"[BOT] Base dir: {base_dir}", flush=True)

# Check strategy file
strategy_file = base_dir / "strategies" / "trend_pullback_v3.py"
print(f"[BOT] Strategy file exists: {strategy_file.exists()}", flush=True)

if not strategy_file.exists():
    print("[BOT] ERROR: Strategy file NOT FOUND", flush=True)
    sys.exit(1)

# Try importing
try:
    print("[BOT] Importing TrendPullbackStrategy...", flush=True)
    sys.path.insert(0, str(base_dir))
    from strategies.trend_pullback_v3 import TrendPullbackStrategy
    print("[BOT] ✅ Strategy imported", flush=True)
except Exception as e:
    print(f"[BOT] ❌ Import failed: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

# Try creating strategy
try:
    print("[BOT] Creating strategy...", flush=True)
    strategy = TrendPullbackStrategy()
    print("[BOT] ✅ Strategy created", flush=True)
except Exception as e:
    print(f"[BOT] ❌ Create failed: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

# Try refreshing universe
try:
    print("[BOT] Refreshing universe...", flush=True)
    strategy.refresh_universe()
    print(f"[BOT] ✅ Universe: {len(strategy.universe)} symbols", flush=True)
except Exception as e:
    print(f"[BOT] ❌ Universe refresh failed: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

# Main loop
print("[BOT] Starting main loop...", flush=True)
cycle = 0
while True:
    cycle += 1
    try:
        print(f"[BOT] Cycle {cycle} starting...", flush=True)
        result = strategy.run_cycle()
        print(f"[BOT] ✅ Cycle {cycle} complete: {result}", flush=True)
    except Exception as e:
        print(f"[BOT] ❌ Cycle {cycle} error: {e}", flush=True)
        traceback.print_exc()

    print("[BOT] Sleeping 60s...", flush=True)
    time.sleep(60)
"""

            with open(bot_script, "w") as f:
                f.write(script_content)

            # Start the bot process with PIPE to capture output
            env = os.environ.copy()
            env["BOT_MODE"] = "paper"
            env["PYTHONUNBUFFERED"] = "1"

            _add_bot_log("Starting bot subprocess...", "log")

            process = subprocess.Popen(
                [sys.executable, str(bot_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).parent.parent),
                env=env,
                text=True,
                bufsize=1,
            )

            _bot_process = process

            # Write PID file immediately
            _write_pid_file(process.pid, "paper")
            _add_bot_log(f"Bot process started with PID {process.pid}", "log")

            # Start background thread to read output
            _read_subprocess_output(process)

            # Wait and verify
            time.sleep(5)

            # Check if process died immediately
            if process.poll() is not None:
                exit_code = process.returncode
                logs = _get_bot_logs(50)

                _delete_pid_file()
                _bot_process = None

                return jsonify({
                    "success": False,
                    "error": f"Bot process died immediately (exit code {exit_code})",
                    "logs": logs,
                    "strategy_check": strategy_check,
                }), 500

            # Process is still running - check for output
            logs = _get_bot_logs(50)
            has_output = len(logs) > 0

            if not has_output:
                return jsonify({
                    "success": True,
                    "message": "Bot process started but no output yet (may be hanging)",
                    "mode": "paper",
                    "pid": process.pid,
                    "logs": logs,
                    "strategy_check": strategy_check,
                    "warning": "Process alive but no stdout - check if hanging",
                })

            return jsonify({
                "success": True,
                "message": "Paper trading started",
                "mode": "paper",
                "pid": process.pid,
                "logs": logs,
                "strategy_check": strategy_check,
            })

        except Exception as e:
            _delete_pid_file()
            _bot_process = None
            return jsonify({"success": False, "error": f"Failed to start bot: {str(e)}"}), 500


@app.route("/api/stop", methods=["POST"])
def stop_bot():
    """Stop the bot process."""
    global _bot_process
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
    _bot_process = None
    return jsonify({"success": True, "message": "Bot stopped"})


@app.route("/api/force-restart", methods=["POST"])
def force_restart():
    """Force kill bot and reset."""
    global _bot_process
    pid = _read_pid_file()
    if pid and _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(1)
        except Exception:
            pass
    _delete_pid_file()
    _bot_process = None
    _clear_bot_logs()
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
    pid = info.get("pid")

    _update_from_database()

    # Get recent log entries
    recent_logs = _get_bot_logs(50)

    # Calculate live unrealized PnL for all open positions
    live_unrealized_pnl = 0
    open_positions_count = 0
    try:
        db = Database()
        open_trades = db.get_open_trades()
        open_positions_count = len(open_trades)

        try:
            from core.bitget_client import BitgetClient
            client = BitgetClient()
            all_tickers = client.get_tickers(product_type="USDT-FUTURES")
            price_map = {}
            if all_tickers:
                for ticker in all_tickers:
                    sym = ticker.get("symbol", "")
                    last = ticker.get("last") or ticker.get("close") or ticker.get("lastPr")
                    if sym and last:
                        try:
                            price_map[sym] = float(last)
                        except:
                            pass

            for t in open_trades:
                current_price = price_map.get(t.symbol, t.entry_price)
                if t.direction == "long" and t.position_size:
                    live_unrealized_pnl += (current_price - t.entry_price) * t.position_size
                elif t.direction == "short" and t.position_size:
                    live_unrealized_pnl += (t.entry_price - current_price) * t.position_size
        except Exception:
            pass
    except Exception:
        pass

    return jsonify({
        "running": is_running,
        "mode": info.get("mode", _mode),
        "cycle_count": 0,
        "last_cycle_time": None,
        "last_error": None,
        "equity": round(_stats_cache["equity"] + live_unrealized_pnl, 2),
        "open_positions": open_positions_count,
        "today_pnl": _stats_cache["today_pnl"],
        "today_trades": _stats_cache["today_trades"],
        "win_rate": _stats_cache["win_rate"],
        "avg_r": _stats_cache["avg_r"],
        "total_risk": _stats_cache["total_risk"],
        "unrealized_pnl": round(live_unrealized_pnl, 2),
        "demo": False,
        "bot_available": BOT_AVAILABLE,
        "backtest_available": BACKTEST_AVAILABLE,
        "pid": pid,
        "started_at": info.get("started_at"),
        "recent_logs": recent_logs,
    })


@app.route("/api/positions")
def get_positions():
    if _is_demo_mode():
        mode = request.args.get("mode", _mode)
        return jsonify({"positions": DEMO_POSITIONS.get(mode, []), "count": len(DEMO_POSITIONS.get(mode, [])), "mode": mode, "demo": True})

    try:
        db = Database()
        open_trades = db.get_open_trades()

        live_prices = {}
        price_source = "entry_fallback"

        try:
            from core.bitget_client import BitgetClient
            client = BitgetClient()
            all_tickers = client.get_tickers(product_type="USDT-FUTURES")
            if all_tickers:
                for ticker in all_tickers:
                    symbol = ticker.get("symbol", "")
                    last_price = ticker.get("last") or ticker.get("close") or ticker.get("lastPr")
                    if symbol and last_price:
                        try:
                            live_prices[symbol] = float(last_price)
                        except (ValueError, TypeError):
                            pass
                price_source = "live_api"
        except Exception as e:
            print(f"Price fetch error: {e}")

        positions = []
        for t in open_trades:
            current_price = live_prices.get(t.symbol, t.entry_price)

            pnl_pct = 0.0
            if t.entry_price and t.entry_price > 0:
                if t.direction == "long":
                    pnl_pct = ((current_price - t.entry_price) / t.entry_price) * 100
                else:
                    pnl_pct = ((t.entry_price - current_price) / t.entry_price) * 100

            unrealized_pnl = 0.0
            if t.position_size:
                if t.direction == "long":
                    unrealized_pnl = (current_price - t.entry_price) * t.position_size
                else:
                    unrealized_pnl = (t.entry_price - current_price) * t.position_size

            r_multiple = 0.0
            stop_distance = abs(t.entry_price - t.stop_loss_price) if t.stop_loss_price else 0
            if stop_distance > 0:
                if t.direction == "long":
                    r_multiple = (current_price - t.entry_price) / stop_distance
                else:
                    r_multiple = (t.entry_price - current_price) / stop_distance

            positions.append({
                "id": t.trade_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": round(t.entry_price, 4),
                "current_price": round(current_price, 4),
                "stop_loss": t.stop_loss_price,
                "take_profit": t.take_profit_price,
                "position_size": round(t.position_size, 4) if t.position_size else 0,
                "position_value": round(t.position_value_usd, 2) if t.position_value_usd else 0,
                "leverage": t.leverage or 1,
                "risk_pct": t.risk_pct or 0,
                "pnl_pct": round(pnl_pct, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "r_multiple": round(r_multiple, 2),
                "price_fresh": t.symbol in live_prices,
            })

        return jsonify({
            "positions": positions,
            "count": len(positions),
            "mode": _mode,
            "demo": False,
            "price_source": price_source,
            "prices_fetched": len(live_prices),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
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
    """Get recent bot log entries from in-memory storage."""
    logs = _get_bot_logs(200)

    # Also check strategy file and data directory
    strategy_check = _check_strategy_file()

    return jsonify({
        "logs": logs,
        "running": _is_bot_running(),
        "strategy_check": strategy_check,
        "log_count": len(logs),
    })


@app.route("/api/debug")
def debug_info():
    """Debug endpoint - shows file system state."""
    return jsonify({
        "cwd": os.getcwd(),
        "base_dir": str(Path(__file__).parent.parent),
        "data_dir_exists": Path("data").exists(),
        "data_files": _list_data_files(),
        "strategy_check": _check_strategy_file(),
        "bot_running": _is_bot_running(),
        "pid": _read_pid_file(),
        "bot_available": BOT_AVAILABLE,
        "backtest_available": BACKTEST_AVAILABLE,
        "python_path": sys.path[:5],
    })


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
    print("TRADE WITH DEZIFY - Diagnostic Dashboard v3")
    print("=" * 60)
    print(f"Bot modules available: {BOT_AVAILABLE}")
    print(f"Backtest engine available: {BACKTEST_AVAILABLE}")
    print("Dashboard: http://127.0.0.1:5000")
    print("Password: Adebayo")
    print("=" * 60)
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)