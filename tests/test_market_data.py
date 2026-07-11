"""
Unit tests for market data fetching and indicator calculations.
"""

import pytest
from unittest.mock import Mock, patch

from core.market_data import MarketData, MarketDataCache
from utils.helpers import (
    calculate_ema,
    calculate_atr,
    calculate_adx,
    is_trending_up,
    is_trending_down,
)


class TestMarketDataCache:
    """Test the in-memory cache."""
    
    def test_cache_set_and_get(self):
        cache = MarketDataCache(max_age_seconds=60)
        candles = [{"close": 100}, {"close": 101}]
        
        cache.set("BTCUSDT", "1H", candles)
        result = cache.get("BTCUSDT", "1H")
        
        assert result == candles
    
    def test_cache_expires(self):
        cache = MarketDataCache(max_age_seconds=0)  # Immediate expiry
        candles = [{"close": 100}]
        
        cache.set("BTCUSDT", "1H", candles)
        result = cache.get("BTCUSDT", "1H")
        
        assert result is None  # Expired immediately
    
    def test_cache_invalidate(self):
        cache = MarketDataCache()
        cache.set("BTCUSDT", "1H", [{"close": 100}])
        
        cache.invalidate("BTCUSDT")
        assert cache.get("BTCUSDT", "1H") is None


class TestEMA:
    """Test EMA calculations."""
    
    def test_ema_basic(self):
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
        ema = calculate_ema(prices, period=5)
        
        # Should have NaN for first 4 values
        assert len(ema) == len(prices)
        assert ema[0] != ema[0]  # NaN check
        assert ema[-1] == 108.0  # Last EMA value for this data
    
    def test_ema_insufficient_data(self):
        prices = [100, 101]
        ema = calculate_ema(prices, period=5)
        
        assert all(e != e for e in ema)  # All NaN


class TestATR:
    """Test ATR calculations."""
    
    def test_atr_basic(self):
        highs = [110, 112, 111, 113, 115]
        lows = [100, 101, 99, 102, 104]
        closes = [105, 108, 106, 110, 112]
        
        atr = calculate_atr(highs, lows, closes, period=3)
        assert atr > 0
        assert isinstance(atr, float)
    
    def test_atr_insufficient_data(self):
        highs = [110]
        lows = [100]
        closes = [105]
        
        atr = calculate_atr(highs, lows, closes, period=3)
        assert atr == 0.0


class TestADX:
    """Test ADX calculations."""
    
    def test_adx_trending(self):
        # Strong uptrend data
        highs = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120]
        lows = [98, 100, 102, 104, 106, 108, 110, 112, 114, 116, 118]
        closes = [99, 101, 103, 105, 107, 109, 111, 113, 115, 117, 119]
        
        adx = calculate_adx(highs, lows, closes, period=5)
        assert adx > 20  # Should detect trend
    
    def test_adx_ranging(self):
        # Sideways data
        highs = [102, 101, 103, 102, 101, 103, 102, 101, 103, 102, 101]
        lows = [98, 99, 97, 98, 99, 97, 98, 99, 97, 98, 99]
        closes = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100]
        
        adx = calculate_adx(highs, lows, closes, period=5)
        assert adx < 20  # Should detect no trend


class TestTrendDetection:
    """Test trend direction detection."""
    
    def test_trending_up(self):
        ema_fast = [100, 101, 102, 103, 104]
        ema_slow = [98, 99, 100, 101, 102]
        
        assert is_trending_up(ema_fast, ema_slow) is True
        assert is_trending_down(ema_fast, ema_slow) is False
    
    def test_trending_down(self):
        ema_fast = [104, 103, 102, 101, 100]
        ema_slow = [106, 105, 104, 103, 102]  # Slow ABOVE fast, both falling
        
        assert is_trending_up(ema_fast, ema_slow) is False
        assert is_trending_down(ema_fast, ema_slow) is True
    
    def test_no_trend(self):
        ema_fast = [100, 100, 100, 100, 100]
        ema_slow = [100, 100, 100, 100, 100]
        
        assert is_trending_up(ema_fast, ema_slow) is False
        assert is_trending_down(ema_fast, ema_slow) is False


class TestMarketDataIntegration:
    """Integration tests requiring API access (mocked)."""
    
    @patch('core.market_data.BitgetClient')
    def test_get_candles(self, mock_client):
        mock_client.return_value.get_candles.return_value = [
            [1609459200000, "100", "110", "95", "105", "1000"],
            [1609462800000, "105", "115", "100", "110", "1200"],
        ]
        
        md = MarketData(client=mock_client.return_value)
        candles = md.get_candles("BTCUSDT", "1H", limit=2, use_cache=False)
        
        assert len(candles) == 2
        assert candles[0]["open"] == 100.0
        assert candles[0]["close"] == 105.0
    
    @patch('core.market_data.BitgetClient')
    def test_get_latest_price(self, mock_client):
        mock_client.return_value.get_candles.return_value = [
            [1609459200000, "100", "110", "95", "105", "1000"],
        ]
        
        md = MarketData(client=mock_client.return_value)
        price = md.get_latest_price("BTCUSDT")
        
        assert price == 105.0