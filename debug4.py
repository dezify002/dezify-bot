from core.bitget_client import BitgetClient
from datetime import datetime

client = BitgetClient()

end_ts = int(datetime(2024, 3, 1).timestamp() * 1000)
start_ts = int(datetime(2024, 1, 1).timestamp() * 1000)

# Test 1: endTime as integer
print("Test 1: endTime as int")
try:
    data = client._request("GET", "/api/v2/mix/market/candles", params={
        "symbol": "BTCUSDT",
        "productType": "USDT-FUTURES",
        "granularity": "4H",
        "limit": 5,
        "endTime": end_ts,
    })
    print(f"  Result: {len(data) if data else 0} candles")
except Exception as e:
    print(f"  Error: {e}")

# Test 2: startTime and endTime
print("\nTest 2: startTime + endTime as strings")
try:
    data = client._request("GET", "/api/v2/mix/market/candles", params={
        "symbol": "BTCUSDT",
        "productType": "USDT-FUTURES",
        "granularity": "4H",
        "limit": 5,
        "startTime": str(start_ts),
        "endTime": str(end_ts),
    })
    print(f"  Result: {len(data) if data else 0} candles")
except Exception as e:
    print(f"  Error: {e}")

# Test 3: No time params (default)
print("\nTest 3: No time params")
try:
    data = client._request("GET", "/api/v2/mix/market/candles", params={
        "symbol": "BTCUSDT",
        "productType": "USDT-FUTURES",
        "granularity": "4H",
        "limit": 5,
    })
    print(f"  Result: {len(data) if data else 0} candles")
    if data:
        first = int(data[0][0])
        last = int(data[-1][0])
        print(f"  Range: {datetime.fromtimestamp(first/1000)} to {datetime.fromtimestamp(last/1000)}")
except Exception as e:
    print(f"  Error: {e}")

# Test 4: startTime only
print("\nTest 4: startTime only")
try:
    data = client._request("GET", "/api/v2/mix/market/candles", params={
        "symbol": "BTCUSDT",
        "productType": "USDT-FUTURES",
        "granularity": "4H",
        "limit": 5,
        "startTime": str(start_ts),
    })
    print(f"  Result: {len(data) if data else 0} candles")
except Exception as e:
    print(f"  Error: {e}")