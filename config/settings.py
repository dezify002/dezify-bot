"""
Central configuration for the crypto trading bot.
All tunable parameters live here.
"""

import os
from dataclasses import dataclass, field
from typing import List


# ─── Environment ─────────────────────────────────────────────
ENV = os.getenv("BOT_ENV", "development")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")  # paper | live


# ─── Bitget API ──────────────────────────────────────────────
@dataclass
class BitgetConfig:
    api_key: str = os.getenv("BITGET_API_KEY", "")
    api_secret: str = os.getenv("BITGET_API_SECRET", "")
    passphrase: str = os.getenv("BITGET_PASSPHRASE", "")
    base_url: str = "https://api.bitget.com"
    sandbox_url: str = "https://api.bitget.com"


BITGET = BitgetConfig()


# ─── Risk Management ─────────────────────────────────────────
@dataclass
class RiskConfig:
    max_account_risk_per_trade: float = 0.01
    max_account_risk_total: float = 0.05
    max_positions: int = 3
    default_leverage: int = 5
    max_leverage: int = 10
    min_leverage: int = 1
    risk_reward_min: float = 2.0
    risk_reward_target: float = 3.0
    atr_multiplier_sl: float = 1.5
    max_sl_pct: float = 0.05       # CHANGED FROM 0.02 TO 0.05
    tp1_ratio: float = 1.0
    tp2_ratio: float = 2.0
    tp3_ratio: float = 3.0


RISK = RiskConfig()


# ─── Strategy Parameters ─────────────────────────────────────
@dataclass
class StrategyConfig:
    primary_timeframe: str = "4H"
    trend_timeframe: str = "1D"
    ema_fast_4h: int = 9
    ema_slow_4h: int = 21
    ema_fast_daily: int = 9
    ema_slow_daily: int = 21
    
    # Regime detector settings
    adx_period: int = 14
    atr_period: int = 14
    min_atr_multiplier: float = 0.3
    
    # ADX threshold for trend strength
    adx_threshold: int = 25
    
    # Volume
    volume_min_multiplier: float = 1.2
    
    # Pullback depth
    pullback_min_depth: float = 0.005
    pullback_max_depth: float = 0.03
    
    # Entry confirmation
    entry_trigger: str = "ema_touch"
    
    # Trend alignment
    trend_alignment_mode: str = "soft"
    
    # Universe refresh
    universe_refresh_interval_minutes: int = 60
    
    # Minimum 24h volume
    min_volume_24h: float = 5_000_000
    
    # Blacklist
    symbol_blacklist: List[str] = field(default_factory=lambda: [
        "TSLAUSDT", "NVDAUSDT", "METAUSDT", "INTCUSDT", "MRVLUSDT", 
        "MUUSDT", "QQQUSDT", "MSTRUSDT", "SNDKUSDT", "SKHYNIXUSDT",
        "SAMSUNGUSDT", "GLWUSDT", "MUUUSDT", "NBISUSDT", "DRAMUSDT",
        "CBRSUSDT", "SPCXUSDT", "KORUUSDT", "ZHIPUUSDT", "SKHYUSDT",
        "XAUUSDT", "XAGUSDT", "CLUSDT", "XAUTUSDT",
        "EWYUSDT", "SOXLUSDT",
        "USUSDT", "TACUSDT", "VELVETUSDT", "ESPORTSUSDT", "CRCLUSDT",
        "BASEDUSDT", "EDGEUSDT", "BZUSDT", "LITEUSDT", "LABUSDT",
        "EVAAUSDT", "TAGUSDT", "DEXEUSDT", "VANRYUSDT", "WLDUSDT",
        "PEPEUSDT", "SUIUSDT", "HYPEUSDT", "TAOUSDT",
    ])


STRATEGY = StrategyConfig()


# ─── Execution ───────────────────────────────────────────────
@dataclass
class ExecutionConfig:
    entry_order_type: str = "limit"
    sl_order_type: str = "stop_market"
    tp_order_type: str = "limit"
    slippage_pct: float = 0.001
    tif: str = "GTC"
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    
    # Additional fields needed by execution_engine.py
    order_type_preference: str = "limit"
    slippage_max_bps: int = 50
    retry_attempts: int = 3
    partial_fill_threshold: float = 0.9


EXECUTION = ExecutionConfig()


# ─── Analytics & Review ──────────────────────────────────────
@dataclass
class AnalyticsConfig:
    weekly_review_day: str = "Sunday"
    monthly_review_day: int = 1
    quarterly_months: List[int] = field(default_factory=lambda: [1, 4, 7, 10])
    log_level: str = "INFO"
    log_format: str = "json"
    database_path: str = "data/trades.db"


ANALYTICS = AnalyticsConfig()


# ─── Logging ─────────────────────────────────────────────────
@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"
    log_to_file: bool = True
    log_dir: str = "logs"


LOGGING = LoggingConfig()


# ─── Database ────────────────────────────────────────────────
@dataclass
class DatabaseConfig:
    path: str = "data/trades.db"
    backup_interval_days: int = 7


DATABASE = DatabaseConfig()


# ─── Backtest ────────────────────────────────────────────────
@dataclass
class BacktestConfig:
    initial_equity: float = 10000.0
    commission_pct: float = 0.0006
    funding_rate_daily: float = 0.0001


BACKTEST = BacktestConfig()


# ─── Config Validation ───────────────────────────────────────
def validate_config() -> dict:
    errors = []
    warnings_list = []
    
    if not BITGET.api_key:
        if TRADING_MODE == "live":
            errors.append("BITGET_API_KEY not set")
        else:
            warnings_list.append("BITGET_API_KEY not set (using sandbox)")
    
    if not BITGET.api_secret:
        if TRADING_MODE == "live":
            errors.append("BITGET_API_SECRET not set")
        else:
            warnings_list.append("BITGET_API_SECRET not set (using sandbox)")
    
    if RISK.max_account_risk_per_trade > 0.05:
        warnings_list.append("Risk per trade > 5% is aggressive")
    
    if RISK.max_leverage > 20:
        warnings_list.append("Leverage > 20x is extremely risky")
    
    if STRATEGY.trend_alignment_mode not in ("hard", "soft"):
        errors.append(f"Invalid trend_alignment_mode: {STRATEGY.trend_alignment_mode}")
    
    if STRATEGY.adx_threshold < 10:
        warnings_list.append("ADX threshold < 10 may produce too many signals")
    
    db_dir = os.path.dirname(DATABASE.path)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir, exist_ok=True)
        except OSError as e:
            errors.append(f"Cannot create database directory: {e}")
    
    if LOGGING.log_to_file:
        if not os.path.exists(LOGGING.log_dir):
            try:
                os.makedirs(LOGGING.log_dir, exist_ok=True)
            except OSError as e:
                errors.append(f"Cannot create log directory: {e}")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings_list,
        "mode": TRADING_MODE,
    }