"""
SQLite database operations for trade persistence and analytics.
Schema designed for the 8-layer explainability model.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from config.settings import DATABASE  # CHANGED FROM ANALYTICS
from data.models import TradeRecord, TradeChecklist, DailyPerformance, RegimeLabel
from utils.logger import get_logger

logger = get_logger(__name__)


class Database:
    """
    SQLite database manager for all bot data.
    Creates tables on first use, provides CRUD operations.
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DATABASE.path  # CHANGED FROM ANALYTICS.database_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()
        logger.info(f"Database initialized: {self.db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_tables(self):
        """Create all tables if they don't exist."""
        with self._get_connection() as conn:
            # Main trades table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    holding_period_hours REAL DEFAULT 0,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    stop_loss_price REAL NOT NULL,
                    take_profit_price REAL,
                    position_size REAL NOT NULL,
                    position_value_usd REAL NOT NULL,
                    leverage INTEGER DEFAULT 1,
                    risk_pct REAL NOT NULL,
                    realized_pnl REAL DEFAULT 0,
                    realized_pnl_pct REAL DEFAULT 0,
                    r_multiple REAL DEFAULT 0,
                    entry_fee REAL DEFAULT 0,
                    exit_fee REAL DEFAULT 0,
                    total_fees REAL DEFAULT 0,
                    funding_paid REAL DEFAULT 0,
                    slippage_entry REAL DEFAULT 0,
                    slippage_exit REAL DEFAULT 0,
                    market_regime TEXT,
                    adx_at_entry REAL DEFAULT 0,
                    atr_at_entry REAL DEFAULT 0,
                    checklist_json TEXT NOT NULL,
                    expected_vs_actual TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Daily performance summary
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_performance (
                    date TEXT PRIMARY KEY,
                    starting_equity REAL NOT NULL,
                    ending_equity REAL NOT NULL,
                    total_trades INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades INTEGER DEFAULT 0,
                    gross_profit REAL DEFAULT 0,
                    gross_loss REAL DEFAULT 0,
                    net_pnl REAL DEFAULT 0,
                    total_fees REAL DEFAULT 0,
                    total_funding REAL DEFAULT 0,
                    max_drawdown_pct REAL DEFAULT 0,
                    max_drawdown_usd REAL DEFAULT 0,
                    peak_equity REAL DEFAULT 0,
                    sharpe_ratio REAL,
                    profit_factor REAL
                )
            """)
            
            # Regime labels (stamped at signal time, NOT reconstructed)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS regime_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    adx REAL DEFAULT 0,
                    atr_pct REAL DEFAULT 0,
                    volume_vs_avg REAL DEFAULT 0,
                    daily_trend TEXT,
                    fourh_trend TEXT
                )
            """)
            
            # Audit log for system events
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    description TEXT,
                    metadata_json TEXT
                )
            """)
            
            # Indexes for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_direction ON trades(direction)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_regime_symbol ON regime_labels(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_regime_timestamp ON regime_labels(timestamp)")
            
            conn.commit()
    
    # ==================== TRADES ====================
    
    def save_trade(self, trade: TradeRecord) -> None:
        """Insert or update a trade record."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades (
                    trade_id, symbol, direction, entry_time, exit_time,
                    holding_period_hours, entry_price, exit_price, stop_loss_price,
                    take_profit_price, position_size, position_value_usd, leverage,
                    risk_pct, realized_pnl, realized_pnl_pct, r_multiple,
                    entry_fee, exit_fee, total_fees, funding_paid,
                    slippage_entry, slippage_exit, market_regime, adx_at_entry,
                    atr_at_entry, checklist_json, expected_vs_actual, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.trade_id,
                trade.symbol,
                trade.direction,
                trade.entry_time.isoformat(),
                trade.exit_time.isoformat() if trade.exit_time else None,
                trade.holding_period_hours,
                trade.entry_price,
                trade.exit_price,
                trade.stop_loss_price,
                trade.take_profit_price,
                trade.position_size,
                trade.position_value_usd,
                trade.leverage,
                trade.risk_pct,
                trade.realized_pnl,
                trade.realized_pnl_pct,
                trade.r_multiple,
                trade.entry_fee,
                trade.exit_fee,
                trade.total_fees,
                trade.funding_paid,
                trade.slippage_entry,
                trade.slippage_exit,
                trade.market_regime,
                trade.adx_at_entry,
                trade.atr_at_entry,
                json.dumps(trade.checklist.to_dict()),
                trade.expected_vs_actual,
                trade.notes,
                trade.created_at.isoformat(),
                datetime.utcnow().isoformat()
            ))
            conn.commit()
        logger.info(f"Trade saved: {trade.trade_id} ({trade.symbol})")
    
    def get_trade(self, trade_id: str) -> Optional[TradeRecord]:
        """Retrieve a single trade by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            
            if not row:
                return None
            
            return self._row_to_trade(row)
    
    def get_open_trades(self) -> List[TradeRecord]:
        """Get all trades that haven't been exited yet."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_time IS NULL ORDER BY entry_time DESC"
            ).fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    def get_trades_by_symbol(self, symbol: str, limit: int = 100) -> List[TradeRecord]:
        """Get trades for a specific symbol."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? ORDER BY entry_time DESC LIMIT ?",
                (symbol, limit)
            ).fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    def get_trades_by_date_range(
        self,
        start: datetime,
        end: datetime
    ) -> List[TradeRecord]:
        """Get trades within a date range."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM trades 
                   WHERE entry_time >= ? AND entry_time <= ? 
                   ORDER BY entry_time DESC""",
                (start.isoformat(), end.isoformat())
            ).fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    def get_all_trades(self, limit: int = 1000) -> List[TradeRecord]:
        """Get all trades, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    def update_trade_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_time: datetime,
        realized_pnl: float,
        exit_fee: float,
        slippage_exit: float = 0,
        exit_reason: str = ""
    ) -> None:
        """Update a trade when it exits."""
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE trades SET
                    exit_price = ?,
                    exit_time = ?,
                    realized_pnl = ?,
                    exit_fee = ?,
                    total_fees = entry_fee + ?,
                    slippage_exit = ?,
                    updated_at = ?,
                    holding_period_hours = ROUND((julianday(?) - julianday(entry_time)) * 24, 2)
                WHERE trade_id = ?
            """, (
                exit_price,
                exit_time.isoformat(),
                realized_pnl,
                exit_fee,
                exit_fee,
                slippage_exit,
                datetime.utcnow().isoformat(),
                exit_time.isoformat(),
                trade_id
            ))
            conn.commit()
        logger.info(f"Trade exited: {trade_id} @ {exit_price} (PnL: {realized_pnl:.2f})")
    
    def _row_to_trade(self, row: sqlite3.Row) -> TradeRecord:
        """Convert a database row to a TradeRecord."""
        data = dict(row)
        data["checklist"] = json.loads(data.pop("checklist_json", "{}"))
        data["entry_time"] = datetime.fromisoformat(data["entry_time"])
        if data.get("exit_time"):
            data["exit_time"] = datetime.fromisoformat(data["exit_time"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return TradeRecord.from_dict(data)
    
    # ==================== DAILY PERFORMANCE ====================
    
    def save_daily_performance(self, perf: DailyPerformance) -> None:
        """Save or update daily performance record."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_performance (
                    date, starting_equity, ending_equity, total_trades,
                    winning_trades, losing_trades, gross_profit, gross_loss,
                    net_pnl, total_fees, total_funding, max_drawdown_pct,
                    max_drawdown_usd, peak_equity, sharpe_ratio, profit_factor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                perf.date, perf.starting_equity, perf.ending_equity,
                perf.total_trades, perf.winning_trades, perf.losing_trades,
                perf.gross_profit, perf.gross_loss, perf.net_pnl,
                perf.total_fees, perf.total_funding, perf.max_drawdown_pct,
                perf.max_drawdown_usd, perf.peak_equity, perf.sharpe_ratio,
                perf.profit_factor
            ))
            conn.commit()
    
    def get_daily_performance(self, date: str) -> Optional[DailyPerformance]:
        """Get performance for a specific date."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM daily_performance WHERE date = ?", (date,)
            ).fetchone()
            if not row:
                return None
            return DailyPerformance(**dict(row))
    
    def get_performance_range(self, start_date: str, end_date: str) -> List[DailyPerformance]:
        """Get daily performance for a date range."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_performance WHERE date >= ? AND date <= ? ORDER BY date",
                (start_date, end_date)
            ).fetchall()
            return [DailyPerformance(**dict(row)) for row in rows]
    
    # ==================== REGIME LABELS ====================
    
    def save_regime_label(self, label: RegimeLabel) -> None:
        """
        Save a regime label stamped at signal time.
        CRITICAL: Must be called BEFORE trade outcome is known.
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO regime_labels (
                    timestamp, symbol, regime, adx, atr_pct,
                    volume_vs_avg, daily_trend, fourh_trend
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                label.timestamp.isoformat(),
                label.symbol,
                label.regime,
                label.adx,
                label.atr_pct,
                label.volume_vs_avg,
                label.daily_trend,
                label.fourh_trend
            ))
            conn.commit()
        logger.info(f"Regime label saved: {label.symbol} = {label.regime}")
    
    def get_regime_labels(
        self,
        symbol: str,
        start: datetime,
        end: datetime
    ) -> List[RegimeLabel]:
        """Get regime labels for analysis."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM regime_labels 
                   WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
                   ORDER BY timestamp""",
                (symbol, start.isoformat(), end.isoformat())
            ).fetchall()
            return [
                RegimeLabel(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    symbol=row["symbol"],
                    regime=row["regime"],
                    adx=row["adx"],
                    atr_pct=row["atr_pct"],
                    volume_vs_avg=row["volume_vs_avg"],
                    daily_trend=row["daily_trend"],
                    fourh_trend=row["fourh_trend"],
                )
                for row in rows
            ]
    
    # ==================== AUDIT LOG ====================
    
    def log_event(self, event_type: str, description: str, metadata: Dict = None):
        """Log a system event (kill switches, errors, config changes)."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO audit_log (timestamp, event_type, description, metadata_json)
                VALUES (?, ?, ?, ?)
            """, (
                datetime.utcnow().isoformat(),
                event_type,
                description,
                json.dumps(metadata or {})
            ))
            conn.commit()
        logger.info(f"AUDIT: {event_type} - {description}")
    
    # ==================== ANALYTICS QUERIES ====================
    
    def get_performance_by_regime(self) -> List[Dict[str, Any]]:
        """
        Break down performance by market regime.
        Answers: Does the strategy rely on one specific condition?
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT 
                    t.market_regime,
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN t.realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
                    SUM(CASE WHEN t.realized_pnl < 0 THEN 1 ELSE 0 END) as losers,
                    SUM(t.realized_pnl) as net_pnl,
                    AVG(t.r_multiple) as avg_r,
                    MAX(t.realized_pnl) as best_trade,
                    MIN(t.realized_pnl) as worst_trade
                FROM trades t
                WHERE t.exit_time IS NOT NULL
                GROUP BY t.market_regime
                ORDER BY net_pnl DESC
            """).fetchall()
            
            results = []
            for row in rows:
                data = dict(row)
                total = data["total_trades"]
                if total > 0:
                    data["win_rate"] = data["winners"] / total
                    data["profit_factor"] = (
                        abs(data["gross_profit"]) / abs(data["gross_loss"])
                        if data["gross_loss"] != 0 else float('inf')
                    )
                results.append(data)
            return results
    
    def get_recent_stats(self, days: int = 30) -> Dict[str, Any]:
        """Get quick stats for the last N days."""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
                    SUM(realized_pnl) as net_pnl,
                    AVG(r_multiple) as avg_r,
                    SUM(total_fees) as total_fees
                FROM trades
                WHERE entry_time >= date('now', '-{} days')
                AND exit_time IS NOT NULL
            """.format(days)).fetchone()
            
            data = dict(row)
            total = data["total_trades"] or 0
            return {
                "period_days": days,
                "total_trades": total,
                "winners": data["winners"] or 0,
                "win_rate": (data["winners"] or 0) / total if total > 0 else 0,
                "net_pnl": data["net_pnl"] or 0,
                "avg_r": data["avg_r"] or 0,
                "total_fees": data["total_fees"] or 0,
            }