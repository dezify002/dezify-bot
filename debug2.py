from core.bitget_client import BitgetClient
from datetime import datetime

client = BitgetClient()

# What we request
start = datetime(2024, 1, 1)
end = datetime(2024, 3, 1)
start_ts = int(start.timestamp() * 1000)
end_ts = int(end.timestamp() * 1000)

print(f"Requested range: {start_ts} to {end_ts}")
print(f"Human: {start.date()} to {end.date()}")

# What Bitget returns
data = client.get_candles('BTCUSDT', '4H', limit=10)
if data:
    first_ts = int(data[0][0])
    last_ts = int(data[-1][0])
    print(f"Bitget range: {first_ts} to {last_ts}")
    print(f"First: {datetime.fromtimestamp(first_ts/1000)}")
    print(f"Last: {datetime.fromtimestamp(last_ts/1000)}")
    print(f"Is first in range? {start_ts <= first_ts <= end_ts}")
    print(f"Is last in range? {start_ts <= last_ts <= end_ts}")