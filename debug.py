from core.bitget_client import BitgetClient

client = BitgetClient()
data = client.get_candles('BTCUSDT', '4H', limit=10)

print('Type:', type(data))
print('Length:', len(data) if data else 0)

if data:
    print('First item type:', type(data[0]))
    print('First item:', data[0])
    if isinstance(data[0], list):
        print('Is list with', len(data[0]), 'elements')
    elif isinstance(data[0], dict):
        print('Keys:', list(data[0].keys()))