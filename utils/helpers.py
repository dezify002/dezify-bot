"""
Shared helper functions for math, time, and data manipulation.
"""

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import numpy as np


def generate_trade_id() -> str:
    """Generate a unique trade identifier."""
    return hashlib.sha256(
        f"{datetime.utcnow().isoformat()}_{uuid.uuid4()}".encode()
    ).hexdigest()[:16].upper()


def timeframe_to_seconds(timeframe: str) -> int:
    """
    Convert timeframe string to seconds.
    
    Args:
        timeframe: "1m", "5m", "15m", "30m", "1H", "4H", "1D"
    
    Returns:
        Seconds in the timeframe
    """
    mapping = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1H": 3600,
        "4H": 14400,
        "1D": 86400,
    }
    return mapping.get(timeframe, 3600)


def timeframe_to_minutes(timeframe: str) -> int:
    """Convert timeframe to minutes."""
    return timeframe_to_seconds(timeframe) // 60


def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """
    Calculate Average True Range.
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: ATR lookback period
    
    Returns:
        ATR value
    """
    if len(highs) < period + 1:
        return 0.0
    
    tr_values = []
    for i in range(1, len(highs)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr_values.append(max(tr1, tr2, tr3))
    
    return float(np.mean(tr_values[-period:]))


def calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """
    Calculate Average Directional Index (ADX).
    Returns 0.0 if insufficient data.
    """
    if len(highs) < period * 2 + 1:
        return 0.0
    
    # +DM and -DM
    plus_dm = []
    minus_dm = []
    tr_values = []
    
    for i in range(1, len(highs)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)
        
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)
        
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        tr_values.append(max(tr1, tr2, tr3))
    
    # Smooth with Wilder's smoothing
    def wilder_smooth(values: List[float], period: int) -> List[float]:
        smoothed = [sum(values[:period]) / period]
        for i in range(period, len(values)):
            smoothed.append((smoothed[-1] * (period - 1) + values[i]) / period)
        return smoothed
    
    tr_smooth = wilder_smooth(tr_values, period)
    plus_dm_smooth = wilder_smooth(plus_dm, period)
    minus_dm_smooth = wilder_smooth(minus_dm, period)
    
    # DI
    plus_di = [(pdm / tr * 100) if tr > 0 else 0 for pdm, tr in zip(plus_dm_smooth, tr_smooth)]
    minus_di = [(mdm / tr * 100) if tr > 0 else 0 for mdm, tr in zip(minus_dm_smooth, tr_smooth)]
    
    # DX
    dx_values = []
    for pdi, mdi in zip(plus_di, minus_di):
        denom = pdi + mdi
        if denom > 0:
            dx_values.append(abs(pdi - mdi) / denom * 100)
        else:
            dx_values.append(0)
    
    # ADX = smoothed DX
    adx_smooth = wilder_smooth(dx_values, period)
    return adx_smooth[-1] if adx_smooth else 0.0


def calculate_ema(prices: List[float], period: int) -> List[float]:
    """
    Calculate Exponential Moving Average.
    
    Args:
        prices: List of price values
        period: EMA period
    
    Returns:
        List of EMA values (same length as input, first 'period' values are NaN)
    """
    if len(prices) < period:
        return [np.nan] * len(prices)
    
    multiplier = 2 / (period + 1)
    ema = [np.nan] * (period - 1)
    
    # First EMA is SMA
    ema.append(sum(prices[:period]) / period)
    
    for i in range(period, len(prices)):
        ema.append((prices[i] - ema[-1]) * multiplier + ema[-1])
    
    return ema


def is_trending_up(ema_fast: List[float], ema_slow: List[float]) -> bool:
    """Check if fast EMA is above slow EMA and both are rising."""
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return False
    
    latest_fast = ema_fast[-1]
    latest_slow = ema_slow[-1]
    prev_fast = ema_fast[-2]
    prev_slow = ema_slow[-2]
    
    return (
        latest_fast > latest_slow and
        latest_fast > prev_fast and
        latest_slow > prev_slow
    )


def is_trending_down(ema_fast: List[float], ema_slow: List[float]) -> bool:
    """Check if fast EMA is below slow EMA and both are falling."""
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return False
    
    latest_fast = ema_fast[-1]
    latest_slow = ema_slow[-1]
    prev_fast = ema_fast[-2]
    prev_slow = ema_slow[-2]
    
    return (
        latest_fast < latest_slow and
        latest_fast < prev_fast and
        latest_slow < prev_slow
    )


def find_swing_highs(prices: List[float], lookback: int = 5) -> List[Tuple[int, float]]:
    """
    Find local swing highs.
    
    Args:
        prices: List of prices
        lookback: Number of bars on each side to confirm swing
    
    Returns:
        List of (index, price) tuples
    """
    swings = []
    for i in range(lookback, len(prices) - lookback):
        window = prices[i - lookback:i + lookback + 1]
        if prices[i] == max(window) and prices[i] > prices[i - 1]:
            swings.append((i, prices[i]))
    return swings


def find_swing_lows(prices: List[float], lookback: int = 5) -> List[Tuple[int, float]]:
    """
    Find local swing lows.
    
    Args:
        prices: List of prices
        lookback: Number of bars on each side to confirm swing
    
    Returns:
        List of (index, price) tuples
    """
    swings = []
    for i in range(lookback, len(prices) - lookback):
        window = prices[i - lookback:i + lookback + 1]
        if prices[i] == min(window) and prices[i] < prices[i - 1]:
            swings.append((i, prices[i]))
    return swings


def pct_change(current: float, previous: float) -> float:
    """Calculate percentage change."""
    if previous == 0:
        return 0.0
    return (current - previous) / previous


def format_price(price: float, decimals: int = 2) -> str:
    """Format price with appropriate decimals."""
    return f"{price:.{decimals}f}"


def get_timeframe_ago(timeframe: str, multiplier: int = 1) -> datetime:
    """Get datetime X timeframes ago."""
    seconds = timeframe_to_seconds(timeframe) * multiplier
    return datetime.utcnow() - timedelta(seconds=seconds)


def chunk_list(lst: List, chunk_size: int) -> List[List]:
    """Split list into chunks."""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns default on zero denominator."""
    return numerator / denominator if denominator != 0 else default


def is_stablecoin(symbol: str) -> bool:
    """Check if a symbol is a stablecoin pair."""
    stablecoins = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "PYUSD"}
    base = symbol.replace("USDT", "").replace("USDC", "").replace("PERP", "")
    return base.upper() in stablecoins or "USD" in base.upper()


def is_leveraged_token(symbol: str) -> bool:
    """Check if symbol is a leveraged token (e.g., BTC3L, ETH3S)."""
    leveraged_suffixes = ("3L", "3S", "5L", "5S", "2L", "2S", "BEAR", "BULL")
    return any(suffix in symbol.upper() for suffix in leveraged_suffixes)


def is_wrapped_duplicate(symbol: str, existing_symbols: List[str]) -> bool:
    """
    Check if this is a wrapped duplicate (e.g., WBTC when BTC exists).
    
    Args:
        symbol: Symbol to check
        existing_symbols: List of already-selected symbols
    
    Returns:
        True if this is a wrapped duplicate of an existing symbol
    """
    wrapped_prefixes = ("W", "C")
    base = symbol.replace("USDT", "").replace("PERP", "")
    
    for prefix in wrapped_prefixes:
        if base.startswith(prefix):
            unwrapped = base[len(prefix):]
            for existing in existing_symbols:
                existing_base = existing.replace("USDT", "").replace("PERP", "")
                if existing_base == unwrapped:
                    return True
    return False