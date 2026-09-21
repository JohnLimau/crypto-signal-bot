import json
import websockets

class BinanceTradeStream:
    def __init__(self, symbol, engine):
        self.symbol = symbol
        self.engine = engine
        self.url = f"wss://stream.binance.com:9443/ws/{symbol}@aggTrade"

    async def run(self):
        while True:
            try:
                print(f"Connecting to Binance stream: {self.symbol}")
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                    async for raw in ws:
                        data = json.loads(raw)

                        # Binance aggTrade:
                        # p = price, q = quantity, m = buyer is market maker
                        price = float(data["p"])
                        qty = float(data["q"])
                        buyer_is_market_maker = data["m"]

                        # If buyer is NOT market maker, the buyer was the aggressor.
                        side = "SELL" if buyer_is_market_maker else "BUY"

                        signal = self.engine.add_trade(price, qty, side)
                        if signal:
                            yield signal
            except Exception as exc:
                print(f"Stream error: {exc}. Reconnecting in 5 seconds...")
                await __import__("asyncio").sleep(5)
