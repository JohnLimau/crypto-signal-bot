# Crypto Signal Bot — Starter

A small Discord bot that watches Binance public aggregate trades and sends a Discord message only when aggressive buy/sell pressure crosses a threshold.

## What this version does

- Reads Binance public `aggTrade` WebSocket data.
- Classifies each trade as aggressive BUY or SELL using Binance's `m` field.
- Keeps a rolling 60-second window.
- Calculates buy-volume vs sell-volume.
- Sends a signal only when one side reaches 65% or more.
- Prevents repeated signals of the same direction.
- No exchange API key is required.
- Does NOT place trades.

Example:

🟢 BUY SIGNAL — BTCUSDT
Entry: 106420.00
Buy pressure: 71.2%
Sell pressure: 28.8%

## Setup

1. Install Python 3.11+.

2. Create a Discord application and bot from the official Discord Developer Portal.

3. Invite the bot to your server with permission to:
   - View Channel
   - Send Messages

4. Copy `.env.example` to `.env`.

5. Put your Discord bot token and channel ID into `.env`.

6. Install packages:

```bash
pip install -r requirements.txt
```

7. Start it:

```bash
python bot.py
```

## GitHub

Create a new GitHub repository and push these files.

IMPORTANT: never commit `.env`. It contains your Discord bot token.

## Important limitation

This is a prototype, not a proven trading strategy.

"Buy pressure > 65%" does NOT mean price must rise. Market orders can be absorbed by limit orders, and order flow can change quickly.

Before considering real trading, add:

- historical data collection
- proper backtesting
- spread/slippage
- fees
- stop-loss / take-profit testing
- higher-timeframe confirmation
- order-book / liquidation data
- persistent signal history
- performance statistics

The goal of this starter is to get the live data → calculation → Discord pipeline working first.
