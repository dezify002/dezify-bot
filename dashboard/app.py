"""
Trade With Dezify - Flask Dashboard
With Force Stop / Reset feature
"""

import os
import sys
import subprocess
import signal
import json
import time
import traceback
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template, jsonify, request, session, send_file

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "dezify_secret_key_2026"

PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "Adebayo")

# =============================================================================
# PATHS
# =============================================================================
BASE_DIR = Path(__file__).parent.parent.absolute()
DATA_DIR = BASE_DIR / "data"
PID_FILE = DATA_DIR / "bot.pid"
STDERR_LOG = DATA_DIR / "bot_stderr.log"
STDOUT_LOG = DATA_DIR / "bot_stdout.log"
STATE_FILE = DATA_DIR / "strategy_state.json"
ARCHIVE_DIR = DATA_DIR / "archive"
DB_FILE = DATA_DIR / "trades.db"

# Starting balance
STARTING_EQUITY = 10000.0

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
    Database = None

try:
    from backtest.engine import BacktestEngine
    BACKTEST_AVAILABLE = True
    print("✅ Backtest engine loaded")
except Exception as e:
    print(f"⚠️ Backtest engine not available: {e}")
    BACKTEST_AVAILABLE = False
    BacktestEngine = None  # type: ignore


# =============================================================================
# PID / PROCESS HELPERS
# =============================================================================

def _read_pid_file() -> Optional[int]:
    try:
        if PID_FILE.exists():
            with open(PID_FILE, "r") as f:
                data = json.load(f)
                return data.get("pid")
    except Exception:
        pass
    return None


def _write_pid_file(pid: int, mode: str = "paper"):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(PID_FILE, "w") as f:
            json.dump({
                "pid": pid,
                "mode": mode,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }, f)
    except Exception as e:
        print(f"Failed to write PID file: {e}")


def _delete_pid_file():
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


def _is_pid_alive(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_bot_running() -> bool:
    """ONLY a live PID means the bot is running."""
    pid = _read_pid_file()
    if pid and _is_pid_alive(pid):
        return True
    if pid:
        _delete_pid_file()
    return False


def _get_bot_info() -> Dict:
    try:
        if PID_FILE.exists():
            with open(PID_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _kill_process(pid: int, timeout: int = 2) -> Dict[str, Any]:
    """Hard kill a process. Returns status dict."""
    result = {"pid": pid, "sigterm": False, "sigkill": False, "alive_after": False}

    if not _is_pid_alive(pid):
        return {**result, "error": "Process already dead"}

    # Try SIGTERM first
    try:
        os.kill(pid, signal.SIGTERM)
        result["sigterm"] = True
        time.sleep(timeout)
        if not _is_pid_alive(pid):
            return result
    except Exception as e:
        result["term_error"] = str(e)

    # SIGKILL fallback
    try:
        os.kill(pid, signal.SIGKILL)
        result["sigkill"] = True
        time.sleep(1)
        result["alive_after"] = _is_pid_alive(pid)
    except Exception as e:
        result["kill_error"] = str(e)
        result["alive_after"] = _is_pid_alive(pid)

    return result


# =============================================================================
# ARCHIVING
# =============================================================================

def _archive_session() -> Dict[str, Any]:
    """
    Archive current session data before reset.
    Returns archive metadata.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    archive_name = f"session_{timestamp}"
    archive_path = ARCHIVE_DIR / f"{archive_name}.json"

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    archive_data = {
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "archive_name": archive_name,
        "archive_path": str(archive_path),
    }

    # Gather session data
    try:
        if Database:
            db = Database()

            # All trades (closed and open)
            all_trades = db.get_all_trades(limit=1000)
            trades_data = []
            for t in all_trades:
                trades_data.append({
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "realized_pnl": t.realized_pnl,
                    "realized_pnl_pct": t.realized_pnl_pct,
                    "r_multiple": t.r_multiple,
                    "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "market_regime": t.market_regime,
                    "is_open": not t.is_closed(),
                })

            # Open positions
            open_trades = db.get_open_trades()
            open_positions = []
            for t in open_trades:
                open_positions.append({
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_price": t.entry_price,
                    "stop_loss": t.stop_loss_price,
                    "take_profit": t.take_profit_price,
                    "position_size": t.position_size,
                    "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                })

            # Equity history
            equity = db.get_equity() or STARTING_EQUITY

            # Performance stats
            closed = [t for t in all_trades if t.is_closed()]
            winners = [t for t in closed if t.is_winner()]

            archive_data["session"] = {
                "total_trades": len(all_trades),
                "closed_trades": len(closed),
                "open_positions": len(open_positions),
                "winners": len(winners),
                "losers": len(closed) - len(winners),
                "win_rate_pct": round(len(winners) / len(closed) * 100, 2) if closed else 0,
                "total_realized_pnl": round(sum(t.realized_pnl for t in closed), 2),
                "avg_r_multiple": round(sum(t.r_multiple for t in closed) / len(closed), 2) if closed else 0,
                "final_equity": round(equity, 2),
                "starting_equity": STARTING_EQUITY,
                "total_return_pct": round((equity - STARTING_EQUITY) / STARTING_EQUITY * 100, 2),
            }
            archive_data["trades"] = trades_data
            archive_data["open_positions_at_archive"] = open_positions

    except Exception as e:
        archive_data["error"] = f"Failed to gather session data: {str(e)}"
        archive_data["session"] = {"error": str(e)}

    # Write archive file
    try:
        with open(archive_path, "w") as f:
            json.dump(archive_data, f, indent=2, default=str)
        archive_data["saved"] = True
        archive_data["file_size_bytes"] = archive_path.stat().st_size
    except Exception as e:
        archive_data["saved"] = False
        archive_data["save_error"] = str(e)

    return archive_data


def _list_archives() -> List[Dict[str, Any]]:
    """List all archived sessions."""
    archives = []
    try:
        if ARCHIVE_DIR.exists():
            for f in sorted(ARCHIVE_DIR.glob("session_*.json"), reverse=True):
                try:
                    stat = f.stat()
                    archives.append({
                        "name": f.stem,
                        "filename": f.name,
                        "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        "size_bytes": stat.st_size,
                        "path": str(f),
                    })
                except Exception:
                    pass
    except Exception:
        pass
    return archives


# =============================================================================
# STATE CLEARING
# =============================================================================

def _clear_session_state() -> Dict[str, Any]:
    """Clear all live session state. Returns status dict."""
    results = {
        "pid_deleted": False,
        "state_deleted": False,
        "logs_cleared": False,
        "db_reset": False,
        "equity_reset": False,
    }

    # 1. Delete PID file
    try:
        _delete_pid_file()
        results["pid_deleted"] = True
    except Exception as e:
        results["pid_error"] = str(e)

    # 2. Delete strategy state file
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        results["state_deleted"] = True
    except Exception as e:
        results["state_error"] = str(e)

    # 3. Clear log files
    try:
        for log_file in [STDERR_LOG, STDOUT_LOG]:
            if log_file.exists():
                log_file.write_text("")
        results["logs_cleared"] = True
    except Exception as e:
        results["logs_error"] = str(e)

    # 4. Reset database — delete and reinitialize
    try:
        if Database:
            db = Database()
            # Close any open trades as "reset" exits
            open_trades = db.get_open_trades()
            for t in open_trades:
                t.exit_price = t.entry_price  # Flat exit
                t.exit_time = datetime.now(timezone.utc)
                t.realized_pnl = 0.0
                t.realized_pnl_pct = 0.0
                t.r_multiple = 0.0
                if t.checklist:
                    t.checklist.exit_reason = "force_reset"
                db.save_trade(t)

            # Reset equity to starting balance
            db.save_equity(STARTING_EQUITY)
            results["equity_reset"] = True
            results["db_reset"] = True
            results["open_positions_closed"] = len(open_trades)
    except Exception as e:
        results["db_error"] = str(e)

    # 5. Clear any run_bot.py script
    try:
        bot_script = DATA_DIR / "run_bot.py"
        if bot_script.exists():
            bot_script.unlink()
        results["script_deleted"] = True
    except Exception as e:
        results["script_error"] = str(e)

    return results


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
# CACHES
# =============================================================================
_positions_cache = []
_trades_cache = []
_stats_cache = {
    "equity": STARTING_EQUITY,
    "open_positions": 0,
    "today_pnl": 0.0,
    "today_trades": 0,
    "win_rate": 0.0,
    "avg_r": 0.0,
    "total_risk": 0.0,
}
_mode = "paper"


def _update_from_database():
    global _trades_cache, _stats_cache
    try:
        if not Database:
            return
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
        equity = latest_perf.ending_equity if latest_perf else STARTING_EQUITY
        _stats_cache.update({
            "equity": round(equity, 2),
            "today_pnl": round(total_pnl, 2),
            "today_trades": len(today_trades),
            "win_rate": round(win_rate, 1),
            "avg_r": round(avg_r, 2),
        })
    except Exception as e:
        print(f"Database update error: {e}")


def _list_data_files() -> List[str]:
    try:
        if DATA_DIR.exists():
            return [str(f.relative_to(DATA_DIR)) for f in DATA_DIR.rglob("*") if f.is_file()]
    except Exception as e:
        return [f"Error: {e}"]
    return []


def _check_strategy_file() -> Dict[str, Any]:
    v3_file = BASE_DIR / "strategies" / "trend_pullback_v3.py"
    v2_file = BASE_DIR / "strategies" / "trend_pullback.py"
    return {
        "v3_exists": v3_file.exists(),
        "v3_size": v3_file.stat().st_size if v3_file.exists() else 0,
        "v2_exists": v2_file.exists(),
        "v2_size": v2_file.stat().st_size if v2_file.exists() else 0,
        "base_dir": str(BASE_DIR),
        "cwd": os.getcwd(),
        "data_files": _list_data_files(),
    }


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


# =============================================================================
# FORCE STOP / RESET ENDPOINT
# =============================================================================

@app.route("/api/force-reset", methods=["POST"])
def force_reset():
    """
    Force Stop / Reset endpoint.

    1. Hard-kill any running bot subprocess (paper/live/backtest)
    2. Archive current session data
    3. Clear all live state (positions, trades, equity, caches)
    4. Reset equity to $10,000
    5. Delete PID and state files

    Returns detailed status of what was done.
    """
    response = {
        "success": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "processes_killed": [],
        "archive": None,
        "state_cleared": None,
        "equity_reset_to": STARTING_EQUITY,
    }

    try:
        # === STEP 1: HARD KILL ALL RUNNING PROCESSES ===
        # Kill paper bot
        pid = _read_pid_file()
        if pid and _is_pid_alive(pid):
            kill_result = _kill_process(pid)
            response["processes_killed"].append({
                "type": "paper_bot",
                **kill_result,
            })

        # Check for any other python processes that might be run_bot.py
        # (belt-and-suspenders: kill any process that has run_bot.py in its cmdline)
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', []) or []
                    if any('run_bot.py' in str(arg) for arg in cmdline):
                        if proc.info['pid'] != os.getpid():  # Don't kill ourselves
                            kill_result = _kill_process(proc.info['pid'])
                            response["processes_killed"].append({
                                "type": "orphan_bot",
                                **kill_result,
                            })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            response["psutil_note"] = "psutil not available, orphan process cleanup skipped"

        # === STEP 2: ARCHIVE CURRENT SESSION ===
        response["archive"] = _archive_session()

        # === STEP 3: CLEAR ALL LIVE STATE ===
        response["state_cleared"] = _clear_session_state()

        # === STEP 4: VERIFY CLEAN STATE ===
        response["verify"] = {
            "pid_file_exists": PID_FILE.exists(),
            "state_file_exists": STATE_FILE.exists(),
            "bot_running": _is_bot_running(),
            "equity_after_reset": None,
        }

        try:
            if Database:
                db = Database()
                response["verify"]["equity_after_reset"] = db.get_equity()
                response["verify"]["open_positions_after_reset"] = len(db.get_open_trades())
        except Exception as e:
            response["verify"]["error"] = str(e)

        response["message"] = (
            f"Reset complete. Archived {response['archive'].get('session', {}).get('total_trades', 0)} trades. "
            f"Equity reset to ${STARTING_EQUITY:,.2f}. "
            f"Killed {len(response['processes_killed'])} process(es)."
        )

    except Exception as e:
        response["success"] = False
        response["error"] = str(e)
        traceback_str = traceback.format_exc()
        response["traceback"] = traceback_str

    return jsonify(response)


@app.route("/api/archives")
def list_archives():
    """List all archived sessions."""
    return jsonify({
        "success": True,
        "archives": _list_archives(),
        "archive_dir": str(ARCHIVE_DIR),
        "archive_dir_exists": ARCHIVE_DIR.exists(),
    })


@app.route("/api/archives/<archive_name>")
def get_archive(archive_name):
    """Get a specific archive's content."""
    try:
        archive_path = ARCHIVE_DIR / f"{archive_name}.json"
        if not archive_path.exists():
            return jsonify({"success": False, "error": "Archive not found"}), 404

        with open(archive_path, "r") as f:
            data = json.load(f)

        return jsonify({"success": True, "archive": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/archives/download/<archive_name>")
def download_archive(archive_name):
    """Download an archive file."""
    try:
        archive_path = ARCHIVE_DIR / f"{archive_name}.json"
        if not archive_path.exists():
            return jsonify({"success": False, "error": "Archive not found"}), 404

        return send_file(
            archive_path,
            mimetype="application/json",
            as_attachment=True,
            download_name=f"{archive_name}.json",
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# EXISTING ROUTES (Start, Stop, Status, etc.)
# =============================================================================

@app.route("/api/start", methods=["POST"])
def start_bot():
    global _mode

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

        try:
            strategy_check = _check_strategy_file()
            if not strategy_check["v3_exists"]:
                return jsonify({
                    "success": False,
                    "error": "Strategy file not found",
                    "strategy_check": strategy_check,
                }), 500

            # Create bot script — NO refresh_universe in startup!
            bot_script = DATA_DIR / "run_bot.py"
            bot_script.parent.mkdir(parents=True, exist_ok=True)

            script_content = rf"""#!/usr/bin/env python3
import sys
import os

os.environ["PYTHONPATH"] = r"{BASE_DIR}"
sys.path.insert(0, r"{BASE_DIR}")

print("[BOT] SCRIPT STARTED", flush=True)
print(f"[BOT] Python: {{sys.executable}}", flush=True)

try:
    print("[BOT] Importing TrendPullbackStrategy...", flush=True)
    from strategies.trend_pullback_v3 import TrendPullbackStrategy
    print("[BOT] ✅ Strategy imported", flush=True)
except Exception as e:
    print(f"[BOT] ❌ Import failed: {{e}}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("[BOT] Creating strategy...", flush=True)
    strategy = TrendPullbackStrategy()
    print("[BOT] ✅ Strategy created", flush=True)
except Exception as e:
    print(f"[BOT] ❌ Create failed: {{e}}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

# NOTE: refresh_universe() is called inside run_cycle(), not here
print("[BOT] Starting main loop...", flush=True)
import time
cycle = 0
while True:
    cycle += 1
    try:
        print(f"[BOT] Cycle {{cycle}} starting...", flush=True)
        result = strategy.run_cycle()
        print(f"[BOT] ✅ Cycle {{cycle}}: {{result}}", flush=True)
    except Exception as e:
        print(f"[BOT] ❌ Cycle {{cycle}} error: {{e}}", flush=True)
        import traceback
        traceback.print_exc()
    time.sleep(60)
"""

            with open(bot_script, "w") as f:
                f.write(script_content)

            # Clear logs
            try:
                STDERR_LOG.write_text("")
                STDOUT_LOG.write_text("")
            except Exception:
                pass

            # Start bot with file logging
            env = os.environ.copy()
            env["PYTHONPATH"] = str(BASE_DIR)
            env["PYTHONUNBUFFERED"] = "1"

            stderr_f = open(STDERR_LOG, "w")
            stdout_f = open(STDOUT_LOG, "w")

            process = subprocess.Popen(
                [sys.executable, "-u", str(bot_script)],
                stdout=stdout_f,
                stderr=stderr_f,
                cwd=str(BASE_DIR),
                env=env,
            )

            _write_pid_file(process.pid, "paper")

            # Check if alive after 3 seconds
            time.sleep(3)
            if process.poll() is not None:
                stderr_f.close()
                stdout_f.close()
                _delete_pid_file()

                stderr_text = ""
                stdout_text = ""
                try:
                    if STDERR_LOG.exists():
                        stderr_text = STDERR_LOG.read_text()
                    if STDOUT_LOG.exists():
                        stdout_text = STDOUT_LOG.read_text()
                except Exception:
                    pass

                return jsonify({
                    "success": False,
                    "error": f"Bot died immediately (exit code {process.returncode})",
                    "stderr": stderr_text[-2000:],
                    "stdout": stdout_text[-1000:],
                }), 500

            stderr_f.close()
            stdout_f.close()

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
    """Graceful stop — SIGTERM then SIGKILL."""
    pid = _read_pid_file()
    killed = False
    if pid and _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if _is_pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
            killed = not _is_pid_alive(pid)
        except Exception:
            pass
    _delete_pid_file()
    return jsonify({"success": True, "message": "Bot stopped", "killed": killed})


@app.route("/api/status")
def get_status():
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

    stderr_text = ""
    stdout_text = ""
    try:
        if STDERR_LOG.exists():
            stderr_text = STDERR_LOG.read_text()[-2000:]
        if STDOUT_LOG.exists():
            stdout_text = STDOUT_LOG.read_text()[-1000:]
    except Exception:
        pass

    live_unrealized_pnl = 0
    open_positions_count = 0
    try:
        if Database:
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
        "stderr": stderr_text,
        "stdout": stdout_text,
    })


@app.route("/api/positions")
def get_positions():
    if _is_demo_mode():
        mode = request.args.get("mode", _mode)
        return jsonify({"positions": DEMO_POSITIONS.get(mode, []), "count": len(DEMO_POSITIONS.get(mode, [])), "mode": mode, "demo": True})

    try:
        if not Database:
            return jsonify({"positions": [], "count": 0, "mode": _mode, "demo": False})
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
        if not Database:
            return jsonify({"error": "Database not available"}), 500
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
        equity = BACKTEST.initial_equity if hasattr(BACKTEST, "initial_equity") else STARTING_EQUITY
        return jsonify({"mode": "backtest", "balance": equity, "source": "backtest_config", "currency": "USDT", "demo": False})


@app.route("/api/bot-logs")
def get_bot_logs():
    stderr_text = ""
    stdout_text = ""
    try:
        if STDERR_LOG.exists():
            stderr_text = STDERR_LOG.read_text()[-3000:]
        if STDOUT_LOG.exists():
            stdout_text = STDOUT_LOG.read_text()[-3000:]
    except Exception:
        pass

    strategy_check = _check_strategy_file()

    return jsonify({
        "stderr": stderr_text,
        "stdout": stdout_text,
        "running": _is_bot_running(),
        "strategy_check": strategy_check,
    })


@app.route("/api/debug")
def debug_info():
    return jsonify({
        "cwd": os.getcwd(),
        "base_dir": str(BASE_DIR),
        "data_dir_exists": DATA_DIR.exists(),
        "data_files": _list_data_files(),
        "strategy_check": _check_strategy_file(),
        "bot_running": _is_bot_running(),
        "pid": _read_pid_file(),
        "bot_available": BOT_AVAILABLE,
        "backtest_available": BACKTEST_AVAILABLE,
        "python_path": sys.path[:5],
        "sys_executable": sys.executable,
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
    print("TRADE WITH DEZIFY - Dashboard with Force Reset")
    print("=" * 60)
    print(f"Bot modules available: {BOT_AVAILABLE}")
    print(f"Backtest engine available: {BACKTEST_AVAILABLE}")
    print("Dashboard: http://127.0.0.1:5000")
    print("Password: Adebayo")
    print("=" * 60)
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)