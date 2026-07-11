"""
Unit tests for Layer 5: Risk Manager.
"""

import pytest
from unittest.mock import Mock

from core.risk_manager import RiskManager, PositionSizing
from core.market_data import MarketData
from data.database import Database


class TestPositionSizing:
    """Test position size calculations."""
    
    def setup_method(self):
        self.mock_md = Mock(spec=MarketData)
        self.mock_db = Mock(spec=Database)
        self.risk = RiskManager(market_data=self.mock_md, database=self.mock_db)
    
    def test_basic_long_position(self):
        """Test basic long position sizing."""
        # Mock candles with known ATR
        self.mock_md.get_candles.return_value = [
            {"high": 110, "low": 90, "close": 100 + i}
            for i in range(50)
        ]
        
        sizing = self.risk.calculate_position_size(
            symbol="BTCUSDT",
            entry_price=100.0,
            direction="long",
            account_equity=10000.0
        )
        
        assert sizing is not None
        assert sizing.size > 0
        assert sizing.position_value > 0
        assert sizing.leverage >= 1
        assert sizing.stop_loss_price < 100.0  # SL below entry for long
        assert sizing.take_profit_price > 100.0  # TP above entry for long
        assert sizing.risk_reward >= 1.5  # At least 1.5:1
    
    def test_basic_short_position(self):
        """Test basic short position sizing."""
        self.mock_md.get_candles.return_value = [
            {"high": 110, "low": 90, "close": 100 - i}
            for i in range(50)
        ]
        
        sizing = self.risk.calculate_position_size(
            symbol="BTCUSDT",
            entry_price=100.0,
            direction="short",
            account_equity=10000.0
        )
        
        assert sizing is not None
        assert sizing.stop_loss_price > 100.0  # SL above entry for short
        assert sizing.take_profit_price < 100.0  # TP below entry for short
    
    def test_risk_limit(self):
        """Test that risk never exceeds configured maximum."""
        self.mock_md.get_candles.return_value = [
            {"high": 200, "low": 50, "close": 100}
            for _ in range(50)
        ]
        
        sizing = self.risk.calculate_position_size(
            symbol="BTCUSDT",
            entry_price=100.0,
            direction="long",
            account_equity=10000.0
        )
        
        assert sizing is not None
        assert sizing.risk_pct <= 0.01  # Max 1% from config
    
    def test_stop_distance_limits(self):
        """Test that stop distance respects min/max bounds."""
        # Very volatile market
        self.mock_md.get_candles.return_value = [
            {"high": 200, "low": 10, "close": 100}
            for _ in range(50)
        ]
        
        sizing = self.risk.calculate_position_size(
            symbol="BTCUSDT",
            entry_price=100.0,
            direction="long",
            account_equity=10000.0
        )
        
        assert sizing is not None
        stop_pct = (100.0 - sizing.stop_loss_price) / 100.0
        assert stop_pct >= 0.005  # Min 0.5%
        assert stop_pct <= 0.05   # Max 5%
    
    def test_insufficient_data(self):
        """Test handling when not enough candles for ATR."""
        self.mock_md.get_candles.return_value = [
            {"high": 110, "low": 90, "close": 100}
        ]  # Only 1 candle
        
        sizing = self.risk.calculate_position_size(
            symbol="BTCUSDT",
            entry_price=100.0,
            direction="long",
            account_equity=10000.0
        )
        
        assert sizing is None
    
    def test_zero_equity(self):
        """Test that zero equity prevents trading."""
        can_trade, reason = self.risk.can_trade()
        assert can_trade is False
        assert "zero_equity" in reason


class TestDrawdownLimits:
    """Test drawdown kill switches."""
    
    def setup_method(self):
        self.mock_md = Mock(spec=MarketData)
        self.mock_db = Mock(spec=Database)
        self.risk = RiskManager(market_data=self.mock_md, database=self.mock_db)
        self.risk.account_equity = 10000.0
        self.risk.daily_start_equity = 10000.0
        self.risk.weekly_start_equity = 10000.0
        self.risk.peak_equity = 10000.0
    
    def test_daily_drawdown_kill(self):
        """Test daily drawdown kill switch."""
        # Lose 6% (above 5% limit)
        self.risk.account_equity = 9400.0
        
        can_trade, reason = self.risk.can_trade()
        assert can_trade is False
        assert "daily_drawdown" in reason
    
    def test_daily_drawdown_safe(self):
        """Test that 4% drawdown doesn't trigger kill switch."""
        self.risk.account_equity = 9600.0
        
        can_trade, reason = self.risk.can_trade()
        assert can_trade is True
    
    def test_weekly_drawdown_kill(self):
        """Test weekly drawdown kill switch."""
        # Set daily safe (4.8% loss), weekly dangerous (17% loss)
        self.risk.daily_start_equity = 9560.0  # 4.8% daily loss
        self.risk.weekly_start_equity = 11000.0  # 17% weekly loss
        self.risk.account_equity = 9100.0
        
        can_trade, reason = self.risk.can_trade()
        assert can_trade is False
        assert "weekly_drawdown" in reason


class TestCorrelationLimits:
    """Test correlation bucketing."""
    
    def setup_method(self):
        self.mock_md = Mock(spec=MarketData)
        self.mock_db = Mock(spec=Database)
        self.risk = RiskManager(market_data=self.mock_md, database=self.mock_db)
    
    def test_bucket_assignment(self):
        """Test that symbols get assigned to correct buckets."""
        assert self.risk.assign_correlation_bucket("BTCUSDT") == "layer1"
        assert self.risk.assign_correlation_bucket("ETHUSDT") == "layer1"
        assert self.risk.assign_correlation_bucket("DOGEUSDT") == "meme"
        assert self.risk.assign_correlation_bucket("UNIUSDT") == "defi"
        assert self.risk.assign_correlation_bucket("FETUSDT") == "ai"
    
    def test_correlation_limit(self):
        """Test that bucket limits are enforced."""
        # Fill up a bucket
        bucket = "meme"
        for i in range(2):
            self.risk.correlation_buckets[bucket].append(f"COIN{i}")
        
        # Third should fail
        assert self.risk.check_correlation_limit("PEPEUSDT", bucket) is False
    
    def test_register_and_close(self):
        """Test position registration and closure."""
        # Must include "bucket" key so close_position knows which bucket to remove from
        self.risk.register_position("BTCUSDT", "layer1", {"size": 0.1, "bucket": "layer1"})
        assert "BTCUSDT" in self.risk.open_positions
        assert "BTCUSDT" in self.risk.correlation_buckets["layer1"]
        
        self.risk.close_position("BTCUSDT", 100.0)
        assert "BTCUSDT" not in self.risk.open_positions
        assert "BTCUSDT" not in self.risk.correlation_buckets["layer1"]


class TestMaxPositions:
    """Test maximum position limits."""
    
    def setup_method(self):
        self.mock_md = Mock(spec=MarketData)
        self.mock_db = Mock(spec=Database)
        self.risk = RiskManager(market_data=self.mock_md, database=self.mock_db)
        self.risk.account_equity = 100000.0
        self.risk.daily_start_equity = 100000.0
        self.risk.weekly_start_equity = 100000.0
    
    def test_max_positions(self):
        """Test that max concurrent positions are enforced."""
        # Fill up to limit
        for i in range(6):
            self.risk.open_positions[f"COIN{i}"] = {"size": 0.1}
        
        can_trade, reason = self.risk.can_trade()
        assert can_trade is False
        assert "max_positions" in reason
    
    def test_under_limit(self):
        """Test that trading is allowed under the limit."""
        for i in range(3):
            self.risk.open_positions[f"COIN{i}"] = {"size": 0.1}
        
        can_trade, _ = self.risk.can_trade()
        assert can_trade is True