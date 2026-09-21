import asyncio
import os
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv

from signal_engine import SignalEngine
from market_data import BinanceTradeStream

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SYMBOLS = [s.strip().lower() for s in os.getenv("SYMBOLS", "btcusdt").split(",") if s.strip()]

async def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing in .env")

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    engines = {symbol: SignalEngine(symbol) for symbol in SYMBOLS}
    last_signal = {symbol: None for symbol in SYMBOLS}

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user}")
        channel = client.get_channel(CHANNEL_ID)
        if channel is None:
            print("WARNING: DISCORD_CHANNEL_ID was not found. The bot will still calculate signals.")
        streams = [BinanceTradeStream(symbol, engines[symbol]) for symbol in SYMBOLS]

        async def consume(stream):
            async for signal in stream.run():
                if signal and signal["side"] != last_signal[stream.symbol]:
                    last_signal[stream.symbol] = signal["side"]
                    msg = format_signal(signal)
                    print(msg)
                    if channel:
                        await channel.send(msg)

        await asyncio.gather(*(consume(s) for s in streams))

    await client.start(DISCORD_TOKEN)

def format_signal(s):
    emoji = "🟢" if s["side"] == "BUY" else "🔴"
    return (
        f"{emoji} **{s['side']} SIGNAL — {s['symbol'].upper()}**\n"
        f"Entry: `{s['price']:.2f}`\n"
        f"Buy pressure: `{s['buy_pct']:.1f}%`\n"
        f"Sell pressure: `{s['sell_pct']:.1f}%`\n"
        f"Trades counted: `{s['trades']}`\n"
        f"Pressure: `{s['pressure']:+.1f}%`\n"
        f"Time: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`\n"
        f"\n*Starter signal only. Not financial advice.*"
    )

if __name__ == "__main__":
    asyncio.run(main())
