from core.bitget_client import BitgetClient
from datetime import datetime

client = BitgetClient()

# Test with endTime
end_ts = int(datetime(2024, 3, 1).timestamp() * 1000)
print(f"Requesting endTime={end_ts}")

params = {
    "symbol": "BTCUSDT",
    "productType": "USDT-FUTURES",
    "granularity": "4H",
    "limit": 10,
    "endTime": str(end_ts),
}

try:
    data = client._request("GET", "/api/v2/mix/market/candles", params=params)
    print(f"Got {len(data) if data else 0} candles")
    if data:
        first = int(data[0][0])
        last = int(data[-1][0])
        print(f"Range: {first} to {last}")
        print(f"First: {datetime.fromtimestamp(first/1000)}")
        print(f"Last: {datetime.fromtimestamp(last/1000)}")
except Exception as e:
    print(f"Error: {e}")