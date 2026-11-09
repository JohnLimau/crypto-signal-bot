# Zeus Crypto Signal Bot V2

An alert-only Discord cryptocurrency signal bot using Binance **public spot market data**. No exchange API keys; no automatic buying, selling or custody.

**What's new:** live Binance aggTrade feed for BTCUSDT + ETHUSDT, rolling 60-second taker volume, closed 1-minute EMA9/EMA21 + RSI14, top-20 order book liquidity, spread/volume filters, confirmation gates, restart-safe SQLite duplicate suppression, reconnection, Discord webhook retry, rotating logs, and a localhost dashboard.

> **Important:** This is an unproven signal heuristic, not a profitable or backtested trading system. The signals can be wrong. “BUY pressure” means taker-side trade volume, not the number of people buying. Aggregate trade *events* are not individual orders or full trade counts. Visible order-book liquidity can vanish or be spoofed. **Do not trade real money based on this without independent validation.**

## Quick start (Windows)

1. Install Python **3.11 or newer** from python.org. During setup tick **Add Python to PATH**.
2. Unzip this project. Copy `.env.example` to `.env`.
3. On your Discord server: **Server Settings → Integrations → Webhooks → New Webhook → Copy Webhook URL**. Paste as `DISCORD_WEBHOOK_URL=` in `.env`. Keep that URL secret. You need the **Webhook**, not a Discord bot token.
4. Double-click `run_windows.bat`, or open terminal in the folder and run:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python bot.py --check
   python bot.py --test-alert
   python bot.py
   ```

5. Open **http://127.0.0.1:8765** in your browser. Allow approximately one 60-second market-data window after startup/reconnection before evaluating signals. **The bot must remain running** to keep monitoring. No paid server required when your PC stays on.

## Quick start (macOS/Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste your Discord Webhook URL
python bot.py --check
python bot.py --test-alert
python bot.py
```

## Docker / always-on server

```bash
cp .env.example .env
# Edit webhook URL in .env
docker compose up -d --build
docker compose logs -f
```

This service must run on an always-on PC, VPS or container host for 24/7 alerts. **GitHub Actions is not a suitable 24/7 host**. Dashboard binds to localhost in the Docker host. If remote access is needed, put it behind a trusted private VPN or authenticated reverse proxy; don't expose the dashboard directly to the open internet.

## Settings

All in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| SYMBOLS | BTCUSDT,ETHUSDT | 1-10 Binance spot pairs |
| WINDOW_SECONDS | 60 | Rolling window of Binance aggregate trades |
| PRESSURE_THRESHOLD | 0.65 | At least 65% aggressive quote volume on one side |
| MIN_TRADES | 100 | At least 100 aggregate events in complete window |
| COOLDOWN_SECONDS | 180 | Minimum time between delivered alerts per symbol |
| CONFIRMATIONS | 2 | Consecutive 10s evaluations before firing |
| NEUTRAL_WINDOWS | 2 | Neutral evaluations required to rearm same direction |
| MIN_CONFIRMING_FACTORS | 2 | Minimum passing checks among EMA, RSI, order book, price move |
| MIN_VOLUME_RATIO | 0.65 | Latest closed minute vs previous 20 minutes |
| CONSOLE_ONLY | false | For local dry runs with no Discord webhook |
| DASHBOARD_HOST | 127.0.0.1 | Bind to local machine only |
| DASHBOARD_PORT | 8765 | Dashboard port |

A candidate must also pass a spread filter and avoid large price moves against taker pressure. Trend/order book/price confirmations are **conditions**, not statistically meaningful confidence scores. Signals are checked every 10s, not on each individual trade. When disconnected or when REST data goes stale, the bot **stops issuing signals** until recovered. The minimum `WINDOW_SECONDS` warmup is 80% so the bot typically needs about 50s of streaming data before the first candidate.

## Local dashboard & alert history

- `http://127.0.0.1:8765/` view live price, buy pressure, RSI, last 1m trend and saved alert history.
- `http://127.0.0.1:8765/api/status` JSON stream state.
- `http://127.0.0.1:8765/api/history` last 50 successfully delivered alerts (stored under `data/signals.sqlite3`).
- `data/signalbot.log` rolling logs. None of these files belong in GitHub.

## Testing

```bash
python -m unittest discover -s tests -v
python -m compileall -q bot.py config.py engine.py indicators.py strategy.py state.py notifier.py dashboard.py
python bot.py --check  # needs live Binance connectivity
python bot.py --test-alert  # posts an explicitly labelled one-time Discord test
```

Tests are offline and deterministic. Full live data/Discord delivery requires your own network access and webhook; neither can be guaranteed by offline tests. Binance's stream connections recycle (approximately every 24 hours); reconnection handling and data warm-up are included. If Binance is blocked on your network, use a permitted alternative data source/venue; don't try to bypass legal/geographical restrictions.

## Safety and limitations

No leveraged/futures trading, no automated execution, no P&L claims. `.env` is gitignored. Webhook URLs are secrets: never paste them into chat, issues, commits or screenshots. Public order book is a snapshot, not a full synchronized order book, and Binance symbols may have different liquidity. This strategy has not been walk-forward tested with realistic fees, latency or slippage.
