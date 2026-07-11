"""
Structured logging setup for the crypto bot.
Logs to console and file in JSON format for analytics.
"""

import logging
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import ANALYTICS


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for machine parsing."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add extra fields if present
        if hasattr(record, "trade_id"):
            log_data["trade_id"] = record.trade_id
        if hasattr(record, "symbol"):
            log_data["symbol"] = record.symbol
        if hasattr(record, "layer"):
            log_data["layer"] = record.layer
        if hasattr(record, "checklist"):
            log_data["checklist"] = record.checklist
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)


class TextFormatter(logging.Formatter):
    """Human-readable text format for development."""
    
    def __init__(self):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger instance.
    
    Usage:
        logger = get_logger(__name__)
        logger.info("Message")
        logger.info("Trade signal", extra={"symbol": "BTCUSDT", "layer": "Layer3"})
    """
    logger = logging.getLogger(name)
    
    # Prevent duplicate handlers if called multiple times
    if logger.handlers:
        return logger
    
    logger.setLevel(getattr(logging, ANALYTICS.log_level.upper(), logging.INFO))
    
    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    
    # File handler
    today = datetime.utcnow().strftime("%Y-%m-%d")
    file_handler = logging.FileHandler(
        log_dir / f"bot_{today}.log",
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    
    # Formatters
    if ANALYTICS.log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = TextFormatter()
    
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger


def log_trade_checklist(
    logger: logging.Logger,
    trade_id: str,
    symbol: str,
    checklist: dict
) -> None:
    """
    Log a complete trade checklist record.
    
    Args:
        logger: Logger instance
        trade_id: Unique trade identifier
        symbol: Trading pair
        checklist: Dict with all 8 layer check results
    """
    logger.info(
        f"Trade checklist recorded for {symbol}",
        extra={
            "trade_id": trade_id,
            "symbol": symbol,
            "layer": "all",
            "checklist": checklist
        }
    )