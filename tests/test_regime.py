"""
Unit tests for Layer 1: Market Regime Detector.
"""

import pytest
from unittest.mock import Mock, patch

from core.regime_detector import RegimeDetector
from core.market_data import MarketData


class TestRegimeDetector:
    """Test the regime detection logic."""
    
    def setup_method(self):
        """Set up mocks for each test."""
        self.mock_md = Mock(spec=MarketData)
        self.detector = RegimeDetector(market_data=self.mock_md)
    
    def test_trending_regime(self):
        """Test detection of trending market."""
        # Mock candles with strong trend
        self.mock_md.get_candles.return_value = [
            {"high": 100 + i*2, "low": 98 + i*2, "close": 99 + i*2, "volume": 1000}
            for i in range(50)
        ]
        self.mock_md.get_volume_24h.return_value = 10_000_000
        
        result = self.detector.evaluate("BTCUSDT")
        
        assert result["tradable"] is True
        assert result["regime"] == "trending"
        assert result["checks"]["adx_above_threshold"] is True
        assert result["checks"]["atr_sufficient"] is True
        assert result["checks"]["volume_sufficient"] is True
    
    def test_ranging_regime(self):
        """Test detection of ranging market (ADX too low)."""
        # Mock candles going sideways
        base = 100
        self.mock_md.get_candles.return_value = [
            {"high": base + (i % 3), "low": base - (i % 3), "close": base + (i % 2), "volume": 1000}
            for i in range(50)
        ]
        self.mock_md.get_volume_24h.return_value = 10_000_000
        
        result = self.detector.evaluate("BTCUSDT")
        
        assert result["tradable"] is False
        assert result["regime"] == "ranging"
        assert result["checks"]["adx_above_threshold"] is False
    
    def test_low_volume(self):
        """Test rejection due to insufficient volume."""
        self.mock_md.get_candles.return_value = [
            {"high": 100 + i*2, "low": 98 + i*2, "close": 99 + i*2, "volume": 100}
            for i in range(50)
        ]
        self.mock_md.get_volume_24h.return_value = 1000  # Way below minimum
        
        result = self.detector.evaluate("BTCUSDT")
        
        assert result["tradable"] is False
        assert result["checks"]["volume_sufficient"] is False
    
    def test_low_atr(self):
        """Test rejection due to insufficient volatility."""
        # Almost flat prices
        self.mock_md.get_candles.return_value = [
            {"high": 100.01, "low": 99.99, "close": 100.00, "volume": 1000}
            for _ in range(50)
        ]
        self.mock_md.get_volume_24h.return_value = 10_000_000
        
        result = self.detector.evaluate("BTCUSDT")
        
        assert result["tradable"] is False
        assert result["checks"]["atr_sufficient"] is False
    
    def test_insufficient_data(self):
        """Test handling when not enough candles available."""
        self.mock_md.get_candles.return_value = [
            {"high": 100, "low": 99, "close": 99.5, "volume": 100}
        ]  # Only 1 candle
        
        result = self.detector.evaluate("BTCUSDT")
        
        assert result["tradable"] is False
        assert result["regime"] == "insufficient_data"
    
    def test_regime_label_trending(self):
        """Test regime label generation for trending market."""
        self.mock_md.get_candles.return_value = [
            {"high": 100 + i*2, "low": 98 + i*2, "close": 99 + i*2, "volume": 1000}
            for i in range(50)
        ]
        self.mock_md.get_volume_24h.return_value = 10_000_000
        
        label = self.detector.get_regime_label("BTCUSDT")
        assert label in ["strong_uptrend", "trending_neutral"]
    
    def test_regime_label_sideways(self):
        """Test regime label for non-trending market."""
        base = 100
        self.mock_md.get_candles.return_value = [
            {"high": base + (i % 3), "low": base - (i % 3), "close": base + (i % 2), "volume": 1000}
            for i in range(50)
        ]
        self.mock_md.get_volume_24h.return_value = 10_000_000
        
        label = self.detector.get_regime_label("BTCUSDT")
        assert label in ["sideways", "low_volatility"]