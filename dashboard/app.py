"""
Trade With Dezify - Flask Dashboard
REAL INTEGRATION with TrendPullbackStrategy + BacktestEngine

NO DEMO DATA unless explicitly requested via ?demo=1
"""

import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template, jsonify, request, session

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "dezify_secret_key_2026"

PASSWORD = "Adebayo"

# =============================================================================
# REAL BOT IMPORTS
# =============================================================================
try:
    from config.settings import BITGET, RISK, BACKTEST
    from strategies.trend_pullback import TrendPullbackStrategy
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
# DEMO DATA - EXPLICITLY GATED, NEVER SILENT
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
# GLOBAL STATE - Shared between dashboard and bot thread
# =============================================================================
class BotState:
    """Thread-safe state container for the trading bot."""

    def __init__(self):
        self.lock = threading.Lock()
        self.strategy = None
        self.bot_thread = None
        self.running = False
        self.mode = "paper"
        self.cycle_count = 0
        self.last_cycle_time = None
        self.last_error = None
        self.backtest_result = None  # type: Optional[Any]

        # Position tracking with cooldown
        self.positions_cache = []
        self.trades_cache = []
        self.stats_cache = {
            "equity": 10000.0,
            "open_positions": 0,
            "today_pnl": 0.0,
            "today_trades": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "total_risk": 0.0,
        }

        # NEW: Track recently exited symbols to prevent immediate re-entry
        self.recently_exited: Dict[str, datetime] = {}

        # NEW: Scan log to show user what's happening
        self.scan_log: List[Dict[str, Any]] = []

    def update_from_strategy(self):
        """Read current state from the strategy instance."""
        if not self.strategy:
            return

        with self.lock:
            self.positions_cache = []
            seen_symbols = set()

            for symbol, pos in self.strategy.open_positions.items():
                # Deduplication: skip if we've already seen this symbol
                if symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)

                signal = pos["signal"]
                current_price = 0
                try:
                    current_price = self.strategy.market_data.get_latest_price(symbol) or signal.entry_price
                except:
                    current_price = signal.entry_price

                pnl_pct = 0
                if signal.direction == "long" and signal.entry_price > 0:
                    pnl_pct = (current_price - signal.entry_price) / signal.entry_price * 100
                elif signal.direction == "short" and signal.entry_price > 0:
                    pnl_pct = (signal.entry_price - current_price) / signal.entry_price * 100

                self.positions_cache.append({
                    "id": pos["trade_id"],
                    "symbol": symbol,
                    "direction": signal.direction,
                    "entry_price": signal.entry_price,
                    "current_price": current_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "position_size": signal.position_size,
                    "position_value": signal.position_value,
                    "leverage": signal.leverage,
                    "risk_pct": signal.risk_pct,
                    "pnl_pct": round(pnl_pct, 2),
                    "entry_time": pos["entry_time"].isoformat() if pos.get("entry_time") else datetime.now(timezone.utc).isoformat(),
                    "r_multiple": round((current_price - signal.entry_price) / (signal.entry_price - signal.stop_loss), 2) if signal.direction == "long" and signal.entry_price != signal.stop_loss else 0,
                })

            self.stats_cache["open_positions"] = len(self.positions_cache)
            self.stats_cache["total_risk"] = sum(p["risk_pct"] for p in self.positions_cache) * 100

    def update_from_database(self):
        """Read trades and stats from database."""
        try:
            db = Database()

            with self.lock:
                all_trades = db.get_all_trades(limit=100)
                self.trades_cache = []
                for t in all_trades:
                    self.trades_cache.append({
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

                self.stats_cache.update({
                    "equity": round(equity, 2),
                    "today_pnl": round(total_pnl, 2),
                    "today_trades": len(today_trades),
                    "win_rate": round(win_rate, 1),
                    "avg_r": round(avg_r, 2),
                })
        except Exception as e:
            print(f"Database update error: {e}")

    def update_from_backtest(self, result):
        """Populate state from backtest result."""
        with self.lock:
            self.backtest_result = result

            self.positions_cache = []
            for t in result.trades:
                if not t.is_closed():
                    self.positions_cache.append({
                        "id": t.trade_id,
                        "symbol": t.symbol,
                        "direction": t.direction,
                        "entry_price": t.entry_price,
                        "current_price": t.entry_price,
                        "stop_loss": t.stop_loss,
                        "take_profit": t.take_profit,
                        "position_size": t.position_size,
                        "position_value": t.position_value,
                        "leverage": t.leverage,
                        "risk_pct": t.risk_pct,
                        "pnl_pct": 0.0,
                        "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                        "r_multiple": 0.0,
                    })

            self.trades_cache = []
            for t in result.trades:
                if t.is_closed():
                    self.trades_cache.append({
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
                        "exit_reason": t.exit_reason,
                    })

            self.stats_cache.update({
                "equity": round(result.final_equity, 2),
                "open_positions": len(self.positions_cache),
                "today_pnl": 0.0,
                "today_trades": 0,
                "win_rate": round(result.win_rate, 1),
                "avg_r": round(result.avg_r, 2),
                "total_risk": sum(p["risk_pct"] for p in self.positions_cache) * 100,
            })

    def get_safe_state(self):
        """Return a copy of current state (thread-safe)."""
        with self.lock:
            return {
                "running": self.running,
                "mode": self.mode,
                "cycle_count": self.cycle_count,
                "last_cycle_time": self.last_cycle_time,
                "last_error": self.last_error,
                "positions": list(self.positions_cache),
                "trades": list(self.trades_cache),
                "stats": dict(self.stats_cache),
                "scan_log": list(self.scan_log),
            }


STATE = BotState()


# =============================================================================
# BOT THREAD FUNCTIONS
# =============================================================================
def _bot_loop_paper():
    """Paper trading loop - runs in background thread."""
    STATE.last_error = None

    try:
        STATE.strategy = TrendPullbackStrategy()
        STATE.strategy.refresh_universe()

        # NEW: Load existing open positions from database on startup
        try:
            db = Database()
            open_trades = db.get_open_trades()
            for trade in open_trades:
                # Reconstruct position from database
                if trade.symbol not in STATE.strategy.open_positions:
                    STATE.strategy.open_positions[trade.symbol] = {
                        "trade_id": trade.trade_id,
                        "signal": trade,  # Simplified - real reconstruction would need full Signal object
                        "entry_time": trade.entry_time,
                    }
            if open_trades:
                print(f"Loaded {len(open_trades)} open positions from database")
        except Exception as e:
            print(f"Could not load open positions: {e}")

        while STATE.running:
            STATE.cycle_count += 1
            STATE.last_cycle_time = datetime.now(timezone.utc).isoformat()

            try:
                result = STATE.strategy.run_cycle()
                print(f"Cycle {STATE.cycle_count}: {result}")

                # NEW: Build scan log from the strategy's activity
                # This shows which symbols were checked and why they passed/failed
                if hasattr(STATE.strategy, 'universe') and STATE.strategy.universe:
                    scan_entry = {
                        "cycle": STATE.cycle_count,
                        "time": datetime.now(timezone.utc).isoformat(),
                        "universe_size": len(STATE.strategy.universe),
                        "open_positions": list(STATE.strategy.open_positions.keys()),
                        "exits": result.get("exits", 0),
                        "entries": result.get("entries", 0),
                    }
                    STATE.scan_log.append(scan_entry)
                    # Keep only last 20 scan logs
                    STATE.scan_log = STATE.scan_log[-20:]

            except Exception as e:
                STATE.last_error = str(e)
                print(f"Cycle error: {e}")

            STATE.update_from_strategy()
            STATE.update_from_database()

            time.sleep(60)

    except Exception as e:
        STATE.last_error = str(e)
        print(f"Bot thread crashed: {e}")
    finally:
        STATE.running = False
        STATE.strategy = None


def _bot_loop_backtest(start_date: str, end_date: str, initial_equity: float):
    """Backtest loop - runs in background thread."""
    STATE.last_error = None

    try:
        if not BACKTEST_AVAILABLE or BacktestEngine is None:
            STATE.last_error = "Backtest engine not available. Check backtest/engine.py exists and has no import errors."
            STATE.running = False
            return

        engine = BacktestEngine(start_date, end_date, initial_equity)
        result = engine.run()

        STATE.update_from_backtest(result)
        STATE.cycle_count = 1
        STATE.last_cycle_time = datetime.now(timezone.utc).isoformat()

        STATE.running = False

    except Exception as e:
        STATE.last_error = str(e)
        print(f"Backtest error: {e}")
    finally:
        STATE.running = False


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
    data = request.get_json()
    mode = data.get("mode", "paper")
    STATE.mode = mode
    return jsonify({"success": True, "mode": mode})


@app.route("/api/start", methods=["POST"])
def start_bot():
    """Start the bot in the current mode."""
    if STATE.running:
        return jsonify({"success": False, "error": "Bot already running"}), 400

    data = request.get_json() or {}
    mode = data.get("mode", STATE.mode)
    STATE.mode = mode
    STATE.running = True
    STATE.cycle_count = 0
    STATE.last_error = None
    STATE.scan_log = []  # Clear scan log on new start

    if mode == "backtest":
        if not BACKTEST_AVAILABLE:
            STATE.running = False
            return jsonify({
                "success": False,
                "error": "Backtest engine not available. Create backtest/engine.py first.",
            }), 503

        start_date = data.get("start_date", "2024-01-01")
        end_date = data.get("end_date", "2024-12-31")
        initial_equity = float(data.get("initial_equity", 10000))

        STATE.bot_thread = threading.Thread(
            target=_bot_loop_backtest,
            args=(start_date, end_date, initial_equity),
            daemon=True
        )
        STATE.bot_thread.start()

        return jsonify({
            "success": True,
            "message": f"Backtest started: {start_date} to {end_date}",
            "mode": "backtest",
        })

    elif mode == "live":
        return jsonify({
            "success": False,
            "error": "Live mode not available through dashboard. Use command line with --mode live."
        }), 403

    else:  # paper
        if not BOT_AVAILABLE:
            STATE.running = False
            return jsonify({
                "success": False,
                "error": "Bot modules not available. Check that strategies/trend_pullback.py and dependencies exist."
            }), 500

        STATE.bot_thread = threading.Thread(
            target=_bot_loop_paper,
            daemon=True
        )
        STATE.bot_thread.start()

        return jsonify({
            "success": True,
            "message": "Paper trading started",
            "mode": "paper"
        })


@app.route("/api/stop", methods=["POST"])
def stop_bot():
    """Stop the bot."""
    STATE.running = False
    STATE.strategy = None
    return jsonify({"success": True, "message": "Bot stopped"})


@app.route("/api/status")
def get_status():
    """Get current bot status with real data (or demo if explicitly requested)."""
    if _is_demo_mode():
        mode = request.args.get("mode", STATE.mode)
        stats = DEMO_STATS.get(mode, DEMO_STATS["paper"])
        return jsonify({
            "running": False,
            "mode": mode,
            "cycle_count": 0,
            "last_cycle_time": None,
            "last_error": None,
            **stats,
            "demo": True,
            "bot_available": BOT_AVAILABLE,
            "backtest_available": BACKTEST_AVAILABLE,
        })

    if STATE.strategy:
        STATE.update_from_strategy()
    STATE.update_from_database()

    state = STATE.get_safe_state()

    return jsonify({
        "running": state["running"],
        "mode": state["mode"],
        "cycle_count": state["cycle_count"],
        "last_cycle_time": state["last_cycle_time"],
        "last_error": state["last_error"],
        "equity": state["stats"]["equity"],
        "open_positions": state["stats"]["open_positions"],
        "today_pnl": state["stats"]["today_pnl"],
        "today_trades": state["stats"]["today_trades"],
        "win_rate": state["stats"]["win_rate"],
        "avg_r": state["stats"]["avg_r"],
        "total_risk": state["stats"]["total_risk"],
        "demo": False,
        "bot_available": BOT_AVAILABLE,
        "backtest_available": BACKTEST_AVAILABLE,
    })


@app.route("/api/positions")
def get_positions():
    """Get open positions from real strategy state (or demo if explicitly requested)."""
    if _is_demo_mode():
        mode = request.args.get("mode", STATE.mode)
        return jsonify({
            "positions": DEMO_POSITIONS.get(mode, []),
            "count": len(DEMO_POSITIONS.get(mode, [])),
            "mode": mode,
            "demo": True,
        })

    if STATE.strategy:
        STATE.update_from_strategy()

    state = STATE.get_safe_state()
    return jsonify({
        "positions": state["positions"],
        "count": len(state["positions"]),
        "mode": state["mode"],
        "demo": False,
    })


@app.route("/api/trades")
def get_trades():
    """Get trade history from database (or demo if explicitly requested)."""
    if _is_demo_mode():
        mode = request.args.get("mode", STATE.mode)
        return jsonify({
            "trades": DEMO_TRADES.get(mode, []),
            "total": len(DEMO_TRADES.get(mode, [])),
            "mode": mode,
            "demo": True,
        })

    STATE.update_from_database()
    state = STATE.get_safe_state()

    return jsonify({
        "trades": state["trades"],
        "total": len(state["trades"]),
        "mode": state["mode"],
        "demo": False,
    })


@app.route("/api/scan-log")
def get_scan_log():
    """NEW: Get the scan log to see which symbols were checked."""
    state = STATE.get_safe_state()
    return jsonify({
        "scan_log": state["scan_log"],
        "universe_size": len(STATE.strategy.universe) if STATE.strategy else 0,
    })


@app.route("/api/trade/<trade_id>")
def get_trade_detail(trade_id):
    """Get full 8-layer explainability for a trade from database."""
    try:
        db = Database()
        trade = db.get_trade(trade_id)

        if not trade:
            return jsonify({"error": "Trade not found"}), 404

        checklist = trade.checklist.to_dict() if trade.checklist else {}

        return jsonify({
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "direction": trade.direction,
            "layers": {
                "layer_1": {
                    "name": "Market Regime",
                    "passed": checklist.get("adx_above_threshold", False),
                    "adx": checklist.get("adx_value", 0),
                    "atr_sufficient": checklist.get("atr_sufficient", False),
                    "volume_sufficient": checklist.get("volume_sufficient", False),
                    "regime": trade.market_regime,
                    "explanation": f"ADX was {checklist.get('adx_value', 0):.1f} (threshold: 25). Market regime: {trade.market_regime}."
                },
                "layer_2": {
                    "name": "Trend Analysis",
                    "passed": checklist.get("daily_ema_aligned", False) or checklist.get("fourh_ema_aligned", False),
                    "daily_trend": "up" if checklist.get("daily_ema_aligned") else "neutral",
                    "fourh_trend": "up" if checklist.get("fourh_ema_aligned") else "neutral",
                    "score": checklist.get("trend_score", 0),
                    "explanation": f"Daily aligned: {checklist.get('daily_ema_aligned', False)}. 4H aligned: {checklist.get('fourh_ema_aligned', False)}. Score: {checklist.get('trend_score', 0)}."
                },
                "layer_3": {
                    "name": "Pullback Detection",
                    "passed": checklist.get("pullback_confirmed", False),
                    "depth": checklist.get("pullback_depth", 0),
                    "explanation": f"Pullback depth: {checklist.get('pullback_depth', 0):.4f}. Confirmed: {checklist.get('pullback_confirmed', False)}."
                },
                "layer_4": {
                    "name": "Entry Trigger",
                    "passed": checklist.get("entry_triggered", False),
                    "entry_price": checklist.get("entry_price"),
                    "stop_loss": checklist.get("stop_loss_price"),
                    "take_profit": checklist.get("take_profit_price"),
                    "risk_reward": checklist.get("risk_reward_ratio", 0),
                    "explanation": f"Entry triggered: {checklist.get('entry_triggered', False)}. Price: {checklist.get('entry_price')}"
                },
                "layer_5": {
                    "name": "Risk Management",
                    "passed": True,
                    "risk_pct": checklist.get("risk_pct", 0),
                    "leverage": checklist.get("leverage", 1),
                    "position_size": checklist.get("position_size", 0),
                    "explanation": f"Risk: {checklist.get('risk_pct', 0)*100:.2f}%. Size: {checklist.get('position_size', 0):.4f}"
                },
                "layer_6": {
                    "name": "Execution",
                    "passed": True,
                    "slippage": checklist.get("slippage_pct", 0),
                    "explanation": "Order executed."
                },
                "layer_7": {
                    "name": "Trade Management",
                    "passed": True,
                    "break_even": checklist.get("break_even_enabled", False),
                    "trailing_stop": checklist.get("trailing_stop_enabled", False),
                    "explanation": f"Exit reason: {checklist.get('exit_reason', 'still_open')}"
                },
                "layer_8": {
                    "name": "Performance Review",
                    "passed": True,
                    "realized_pnl": trade.realized_pnl,
                    "r_multiple": trade.r_multiple,
                    "explanation": f"PnL: ${trade.realized_pnl:.2f}. R: {trade.r_multiple:.2f}R"
                }
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/balance")
def get_balance():
    """Get account balance based on mode."""
    mode = request.args.get("mode", STATE.mode)

    if _is_demo_mode():
        stats = DEMO_STATS.get(mode, DEMO_STATS["paper"])
        return jsonify({
            "mode": mode,
            "balance": stats["equity"],
            "source": "demo",
            "currency": "USDT",
            "demo": True,
        })

    if mode == "live":
        try:
            client = BitgetClient()
            balance = client.get_account_equity()
            return jsonify({
                "mode": "live",
                "balance": balance,
                "source": "bitget_api",
                "currency": "USDT",
                "demo": False,
            })
        except Exception as e:
            return jsonify({
                "mode": "live",
                "balance": 0,
                "error": f"Could not fetch live balance: {str(e)}",
                "source": "error",
                "demo": False,
            })

    elif mode == "paper":
        equity = 10000.0
        if STATE.strategy:
            try:
                equity = STATE.strategy._get_account_equity()
            except:
                pass

        return jsonify({
            "mode": "paper",
            "balance": equity,
            "source": "paper_account",
            "currency": "USDT",
            "demo": False,
        })

    else:  # backtest
        if STATE.backtest_result:
            equity = STATE.backtest_result.final_equity
        else:
            equity = BACKTEST.initial_equity if hasattr(BACKTEST, "initial_equity") else 10000.0

        return jsonify({
            "mode": "backtest",
            "balance": equity,
            "source": "backtest_result" if STATE.backtest_result else "backtest_config",
            "currency": "USDT",
            "demo": False,
        })


@app.route("/api/backtest/result")
def get_backtest_result():
    """Get detailed backtest results."""
    if not STATE.backtest_result:
        return jsonify({"error": "No backtest result available. Run a backtest first."}), 404

    result = STATE.backtest_result
    return jsonify({
        "start_date": result.start_date,
        "end_date": result.end_date,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "total_return_pct": result.total_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate": result.win_rate,
        "avg_r": result.avg_r,
        "avg_winner_r": result.avg_winner_r,
        "avg_loser_r": result.avg_loser_r,
        "profit_factor": result.profit_factor,
    })


if __name__ == "__main__":
    print("=" * 60)
    print("TRADE WITH DEZIFY - Real Integration Dashboard")
    print("=" * 60)
    print(f"Bot modules available: {BOT_AVAILABLE}")
    print(f"Backtest engine available: {BACKTEST_AVAILABLE}")
    print("Dashboard: http://127.0.0.1:5000")
    print("Password: Adebayo")
    print("=" * 60)
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)