"""
Market data fetching and caching layer.
Handles OHLCV candles, volume, and basic preprocessing.
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import numpy as np

from core.bitget_client import BitgetClient
from utils.helpers import timeframe_to_seconds
from utils.logger import get_logger

logger = get_logger(__name__)


class MarketDataCache:
    """
    In-memory cache for candle data to reduce API calls.
    Refreshes automatically when data is stale.
    """
    
    def __init__(self, max_age_seconds: int = 60):
        self._cache: Dict[str, Dict[str, List[Dict]]] = defaultdict(dict)
        self._last_update: Dict[str, Dict[str, float]] = defaultdict(dict)
        self._max_age = max_age_seconds
    
    def get(self, symbol: str, timeframe: str) -> Optional[List[Dict]]:
        """Get cached candles if fresh."""
        key = f"{symbol}_{timeframe}"
        last = self._last_update.get(symbol, {}).get(timeframe, 0)
        
        if time.time() - last < self._max_age:
            return self._cache.get(symbol, {}).get(timeframe)
        return None
    
    def set(self, symbol: str, timeframe: str, candles: List[Dict]):
        """Store candles in cache."""
        self._cache[symbol][timeframe] = candles
        self._last_update[symbol][timeframe] = time.time()
    
    def invalidate(self, symbol: str = None):
        """Clear cache for a symbol or all."""
        if symbol:
            self._cache.pop(symbol, None)
            self._last_update.pop(symbol, None)
        else:
            self._cache.clear()
            self._last_update.clear()


class MarketData:
    """
    High-level market data interface.
    Fetches from Bitget API with caching and preprocessing.
    """
    
    def __init__(self, client: Optional[BitgetClient] = None):
        self.client = client or BitgetClient()
        self.cache = MarketDataCache(max_age_seconds=30)  # 30s cache for live data
    
    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        use_cache: bool = True
    ) -> List[Dict]:
        """
        Fetch OHLCV candles for a symbol.
        
        Args:
            symbol: e.g., "BTCUSDT"
            timeframe: "1m", "5m", "15m", "30m", "1H", "4H", "1D"
            limit: Number of candles (max 1000)
            use_cache: Whether to use cached data if available
        
        Returns:
            List of candle dicts with keys: timestamp, open, high, low, close, volume
        """
        # Check cache
        if use_cache:
            cached = self.cache.get(symbol, timeframe)
            if cached and len(cached) >= limit:
                return cached[-limit:]
        
        # Fetch from API
        try:
            raw_candles = self.client.get_candles(
                symbol=symbol,
                granularity=timeframe,
                limit=limit
            )
            
            # Normalize to standard format
            candles = self._normalize_candles(raw_candles)
            
            # Update cache
            self.cache.set(symbol, timeframe, candles)
            
            return candles[-limit:]
            
        except Exception as e:
            logger.error(f"Failed to fetch candles for {symbol} {timeframe}: {e}")
            # Return cached data even if stale as fallback
            cached = self.cache.get(symbol, timeframe)
            if cached:
                logger.warning(f"Using stale cache for {symbol} {timeframe}")
                return cached[-limit:]
            return []
    
    def get_multi_timeframe(
        self,
        symbol: str,
        timeframes: List[str],
        limit: int = 100
    ) -> Dict[str, List[Dict]]:
        """
        Fetch candles for multiple timeframes at once.
        Used for trend alignment checks (Daily + 4H).
        """
        result = {}
        for tf in timeframes:
            result[tf] = self.get_candles(symbol, tf, limit)
        return result
    
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get the most recent close price."""
        candles = self.get_candles(symbol, "1m", limit=1, use_cache=True)
        if candles:
            return candles[-1]["close"]
        return None
    
    def get_volume_24h(self, symbol: str) -> float:
        """Get 24h trading volume in USD."""
        try:
            tickers = self.client.get_tickers()
            for ticker in tickers:
                if ticker.get("symbol") == symbol:
                    # Bitget returns volume in base asset or quote asset
                    # We need to handle both cases
                    volume = float(ticker.get("baseVolume", 0))
                    last_price = float(ticker.get("lastPr", 0))
                    return volume * last_price
        except Exception as e:
            logger.error(f"Failed to get 24h volume for {symbol}: {e}")
        return 0.0
    
    def get_spread_bps(self, symbol: str) -> float:
        """
        Get current bid-ask spread in basis points.
        Used for universe filtering (exclude illiquid pairs).
        """
        try:
            tickers = self.client.get_tickers()
            for ticker in tickers:
                if ticker.get("symbol") == symbol:
                    bid = float(ticker.get("bidPr", 0))
                    ask = float(ticker.get("askPr", 0))
                    if bid > 0 and ask > 0:
                        mid = (bid + ask) / 2
                        spread = (ask - bid) / mid * 10000  # Convert to bps
                        return spread
        except Exception as e:
            logger.error(f"Failed to get spread for {symbol}: {e}")
        return float('inf')  # Return infinity if can't determine
    
    def get_funding_rate(self, symbol: str) -> float:
        """Get current funding rate as decimal (e.g., 0.0001 = 0.01%)."""
        try:
            data = self.client.get_funding_rate(symbol)
            return float(data.get("fundingRate", 0))
        except Exception as e:
            logger.error(f"Failed to get funding rate for {symbol}: {e}")
        return 0.0
    
    def get_open_interest(self, symbol: str) -> float:
        """Get open interest in base asset units."""
        try:
            data = self.client.get_open_interest(symbol)
            return float(data.get("openInterest", 0))
        except Exception as e:
            logger.error(f"Failed to get open interest for {symbol}: {e}")
        return 0.0
    
    def _normalize_candles(self, raw_candles: List) -> List[Dict]:
        """
        Convert Bitget candle format to standard OHLCV.
        
        Bitget returns: [timestamp, open, high, low, close, volume]
        """
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
    
    def get_price_history(
        self,
        symbol: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[Dict]:
        """
        Fetch price history for a specific date range.
        May require multiple API calls if range is large.
        """
        all_candles = []
        current = end_time
        
        # Work backwards from end_time
        while current > start_time:
            candles = self.get_candles(symbol, timeframe, limit=1000, use_cache=False)
            if not candles:
                break
            
            for c in reversed(candles):
                candle_time = datetime.fromtimestamp(c["timestamp"] / 1000)
                if start_time <= candle_time <= end_time:
                    all_candles.insert(0, c)
                elif candle_time < start_time:
                    break
            
            # Move current back by the amount of data we fetched
            if candles:
                oldest_ts = candles[0]["timestamp"] / 1000
                current = datetime.fromtimestamp(oldest_ts)
            else:
                break
            
            # Avoid rate limit
            time.sleep(0.1)
        
        return all_candles
    
    def calculate_returns(self, candles: List[Dict]) -> List[float]:
        """Calculate period returns from candles."""
        closes = [c["close"] for c in candles]
        returns = []
        for i in range(1, len(closes)):
            if closes[i-1] != 0:
                returns.append((closes[i] - closes[i-1]) / closes[i-1])
        return returns
    
    def calculate_volatility(self, candles: List[Dict], period: int = 20) -> float:
        """Calculate annualized volatility from returns."""
        returns = self.calculate_returns(candles)
        if len(returns) < period:
            return 0.0
        recent_returns = returns[-period:]
        std = np.std(recent_returns)
        # Annualize (assuming hourly candles for 4H, adjust as needed)
        return std * np.sqrt(365 * 6)  # 6 periods per day for 4H