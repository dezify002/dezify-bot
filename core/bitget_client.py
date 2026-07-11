"""
Bitget API V2 client wrapper.
Handles authentication, request signing, rate limiting, and error handling.
"""

import base64
import hmac
import hashlib
import json
import time
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import requests

from config.settings import BITGET, TRADING_MODE
from utils.logger import get_logger

logger = get_logger(__name__)


class BitgetAuthError(Exception):
    """Raised when API authentication fails."""
    pass


class BitgetAPIError(Exception):
    """Raised when Bitget API returns an error."""
    pass


class BitgetRateLimitError(Exception):
    """Raised when rate limit is hit."""
    pass


class BitgetClient:
    """
    Bitget V2 API client for perpetual futures.
    
    Docs: https://www.bitget.com/support/articles/360007298154
    V2 Endpoint: https://api.bitget.com
    """
    
    def __init__(self):
        self.api_key = BITGET.api_key
        self.api_secret = BITGET.api_secret
        self.passphrase = BITGET.passphrase
        self.base_url = BITGET.base_url
        self.paper_mode = TRADING_MODE != "live"
        
        # Only require credentials for live trading
        if not self.paper_mode and not all([self.api_key, self.api_secret, self.passphrase]):
            logger.error("Bitget API credentials not configured")
            raise BitgetAuthError("Missing API credentials. Check your .env file.")
        
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        
        # Rate limiting state
        self._last_request_time = 0.0
        self._min_request_interval = 0.1  # 100ms between requests (10 req/sec max)
        
        env_str = "sandbox" if self.paper_mode else "live"
        logger.info(f"BitgetClient initialized | env={env_str}")
    
    def _generate_signature(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """
        Generate HMAC-SHA256 signature per Bitget V2 spec.
        Format: timestamp + method.upper() + request_path + body
        """
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode("utf-8")
    
    def _get_headers(self, method: str, request_path: str, body: str = "") -> Dict[str, str]:
        """Build authenticated headers for every request."""
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(timestamp, method, request_path, body)
        
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self.passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }
    
    def _rate_limit(self):
        """Simple rate limiter to avoid hitting API limits."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()
    
    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        body: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Make an authenticated request to Bitget API.
        """
        self._rate_limit()
        
        # Build full URL
        url = f"{self.base_url}{endpoint}"
        
        # Build request path for signature (includes query string)
        request_path = endpoint
        if params:
            query_string = urlencode(sorted(params.items()))
            url = f"{url}?{query_string}"
            request_path = f"{endpoint}?{query_string}"
        
        # Prepare body
        body_str = json.dumps(body) if body else ""
        
        # Get auth headers
        headers = self._get_headers(method, request_path, body_str)
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                data=body_str if body else None,
                timeout=10
            )
            
            # Handle rate limit
            if response.status_code == 429:
                raise BitgetRateLimitError("Rate limit exceeded. Backing off.")
            
            # Parse response
            data = response.json()
            
            # Bitget wraps responses: {"code": "00000", "msg": "success", "data": {...}}
            if data.get("code") != "00000":
                error_msg = f"Bitget API error: {data.get('msg', 'Unknown')} (code: {data.get('code')})"
                logger.error(error_msg)
                raise BitgetAPIError(error_msg)
            
            return data.get("data", {})
            
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout: {method} {endpoint}")
            raise BitgetAPIError("Request timeout")
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error: {method} {endpoint}")
            raise BitgetAPIError("Connection error")
    
    # ==================== MARKET DATA ====================
    
    def get_tickers(self, product_type: str = "USDT-FUTURES") -> List[Dict]:
        """Get all ticker prices for perpetual futures."""
        endpoint = "/api/v2/mix/market/tickers"
        params = {"productType": product_type}
        return self._request("GET", endpoint, params=params)
    
    def get_candles(
        self,
        symbol: str,
        granularity: str = "1H",
        limit: int = 100,
        product_type: str = "USDT-FUTURES"
    ) -> List[Dict]:
        """Fetch historical candlestick (OHLCV) data."""
        endpoint = "/api/v2/mix/market/candles"
        params = {
            "symbol": symbol,
            "productType": product_type,
            "granularity": granularity,
            "limit": min(limit, 1000),
        }
        return self._request("GET", endpoint, params=params)
    
    def get_funding_rate(self, symbol: str, product_type: str = "USDT-FUTURES") -> Dict:
        """Get current funding rate for a symbol."""
        endpoint = "/api/v2/mix/market/funding-rate"
        params = {
            "symbol": symbol,
            "productType": product_type,
        }
        return self._request("GET", endpoint, params=params)
    
    def get_open_interest(self, symbol: str, product_type: str = "USDT-FUTURES") -> Dict:
        """Get open interest data."""
        endpoint = "/api/v2/mix/market/open-interest"
        params = {
            "symbol": symbol,
            "productType": product_type,
        }
        return self._request("GET", endpoint, params=params)
    
    # ==================== ACCOUNT ====================
    
    def get_account(self, symbol: str = None, product_type: str = "USDT-FUTURES") -> Dict:
        """Get account balance and margin info."""
        endpoint = "/api/v2/mix/account/account"
        params = {"productType": product_type}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", endpoint, params=params)
    
    def get_positions(self, product_type: str = "USDT-FUTURES") -> List[Dict]:
        """Get all open positions."""
        endpoint = "/api/v2/mix/position/all-position"
        params = {"productType": product_type}
        return self._request("GET", endpoint, params=params)
    
    # ==================== TRADING ====================
    
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: float,
        price: Optional[float] = None,
        margin_mode: str = "crossed",
        product_type: str = "USDT-FUTURES",
        **kwargs
    ) -> Dict:
        """Place a new order."""
        endpoint = "/api/v2/mix/order/place-order"
        
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginMode": margin_mode,
            "side": side,
            "orderType": order_type,
            "size": str(size),
        }
        
        if order_type == "limit" and price is not None:
            body["price"] = str(price)
        
        body.update(kwargs)
        
        logger.info(f"Placing order: {side} {size} {symbol} @ {price or 'MARKET'}")
        return self._request("POST", endpoint, body=body)
    
    def cancel_order(self, symbol: str, order_id: str, product_type: str = "USDT-FUTURES") -> Dict:
        """Cancel an existing order."""
        endpoint = "/api/v2/mix/order/cancel-order"
        body = {
            "symbol": symbol,
            "productType": product_type,
            "orderId": order_id,
        }
        return self._request("POST", endpoint, body=body)
    
    def get_order_detail(self, symbol: str, order_id: str, product_type: str = "USDT-FUTURES") -> Dict:
        """Get details of a specific order."""
        endpoint = "/api/v2/mix/order/detail"
        params = {
            "symbol": symbol,
            "productType": product_type,
            "orderId": order_id,
        }
        return self._request("GET", endpoint, params=params)
    
    def close_position(
        self,
        symbol: str,
        hold_side: str,
        product_type: str = "USDT-FUTURES"
    ) -> Dict:
        """Close a position (one-click close)."""
        endpoint = "/api/v2/mix/order/close-positions"
        body = {
            "symbol": symbol,
            "productType": product_type,
            "holdSide": hold_side,
        }
        logger.info(f"Closing position: {hold_side} {symbol}")
        return self._request("POST", endpoint, body=body)
    
    def set_leverage(
        self,
        symbol: str,
        leverage: int,
        margin_mode: str = "crossed",
        product_type: str = "USDT-FUTURES"
    ) -> Dict:
        """Set leverage for a symbol."""
        endpoint = "/api/v2/mix/account/set-leverage"
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginMode": margin_mode,
            "leverage": str(leverage),
        }
        return self._request("POST", endpoint, body=body)
    
    # ==================== UTILITIES ====================
    
    def get_server_time(self) -> int:
        """Get Bitget server time in milliseconds."""
        endpoint = "/api/v2/public/time"
        data = self._request("GET", endpoint)
        return int(data.get("serverTime", 0))
    
    def test_connection(self) -> bool:
        """Test if API connection and authentication work."""
        try:
            self.get_server_time()
            logger.info("Bitget API connection test: SUCCESS")
            return True
        except Exception as e:
            logger.error(f"Bitget API connection test: FAILED - {e}")
            return False
    
    # ==================== ACCOUNT EQUITY ====================
    
    def get_account_equity(self) -> float:
        """
        Get account equity from Bitget API.
        ALWAYS tries the real API first, regardless of paper_mode.
        Only falls back to demo $10,000 if the API call actually fails.
        """
        # Check if we have credentials to even attempt an API call
        has_credentials = all([self.api_key, self.api_secret, self.passphrase])
        
        if not has_credentials:
            logger.warning("No API credentials configured, using fallback equity")
            return 10000.0
        
        # Try to get real balance from API
        try:
            account = self.get_account()
            
            # Bitget returns different structures depending on account type
            # Try common equity fields
            equity = (
                account.get("equity") 
                or account.get("available") 
                or account.get("usdtEquity")
                or account.get("accountEquity")
            )
            
            if equity is not None:
                equity_float = float(equity)
                logger.info(f"Account equity fetched: ${equity_float:,.2f}")
                return equity_float
            
            # If no equity field found, log what we got and fallback
            logger.warning(f"Could not find equity field in account response: {account}")
            return 10000.0
            
        except BitgetAuthError as e:
            logger.error(f"Authentication failed: {e}")
            return 10000.0
        except BitgetAPIError as e:
            logger.error(f"API error fetching equity: {e}")
            return 10000.0
        except Exception as e:
            logger.error(f"Unexpected error fetching equity: {e}")
            return 10000.0