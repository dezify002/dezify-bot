from core.bitget_client import BitgetClient
from datetime import datetime

client = BitgetClient()

# Fetch with endTime
end_ts = int(datetime(2024, 3, 1).timestamp() * 1000)
data = client._request("GET", "/api/v2/mix/market/candles", params={
    "symbol": "BTCUSDT",
    "productType": "USDT-FUTURES",
    "granularity": "4H",
    "limit": 200,
    "endTime": str(end_ts),
})

if data:
    first_ts = int(data[0][0])
    last_ts = int(data[-1][0])
    print(f"First candle: {datetime.fromtimestamp(first_ts/1000)}")
    print(f"Last candle: {datetime.fromtimestamp(last_ts/1000)}")
    print(f"Total: {len(data)} candles")
else:
    print("No data")