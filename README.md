# Zeus Crypto Signal Bot V3 — Off-White Command Center

An alert-only Discord cryptocurrency signal bot using Binance **public spot market data**. No exchange API keys; no automatic buying, selling or custody.

**What's new:** live Binance aggTrade feed for BTCUSDT + ETHUSDT, rolling 60-second taker volume, closed 1-minute EMA9/EMA21 + RSI14, top-20 order book liquidity, spread/volume filters, confirmation gates, restart-safe SQLite duplicate suppression, reconnection, Discord webhook retry, rotating logs, and a localhost dashboard.

> **Important:** This is an unproven signal heuristic, not a profitable or backtested trading system. The signals can be wrong. “BUY pressure” means taker-side trade volume, not the number of people buying. Aggregate trade *events* are not individual orders or full trade counts. Visible order-book liquidity can vanish or be spoofed. **Do not trade real money based on this without independent validation.**

## New: ZEUS Command Center (V3)

Open **http://127.0.0.1:8765/** on the **same computer running the bot**.

The warm off-white, responsive web app includes:

- **Overview:** market readiness, saved alert counts, five-minute follow-through, process uptime.
- **Live markets:** sampled spot-price history (last ~30 minutes), rolling taker buy/sell pressure, 1-minute EMA/RSI, 20-level book imbalance, spread, and data freshness per symbol.
- **Follow-through:** for every saved alert, a periodic live sample at 5/15/30/60 minutes; records the *directional spot percentage move*, whether it moved positively, and labels missed samples. This is **NOT historical backtesting, a win-probability model, or realized P&L**.
- **Research journal:** symbol/direction/period filters, pagination, CSV download with observed/missed/pending outcomes.
- **System health:** WebSocket connectivity, per-market freshness, Discord/console send counters, last delivery times, process uptime, and current read-only settings.

**How outcomes work:** a sampled live market price within 40 seconds after the horizon is used. If there isn't one because the laptop is sleeping, offline, disconnected or otherwise too late, the horizon is marked **missed** and excluded from observed-direction percentages. Existing alerts automatically appear in history, but prior follow-through can't be reconstructed from data the bot never collected. Start the upgraded bot and let it run to collect outcomes.

**Important security:** this is a read-only service, but alert records and telemetry are personal information. Keep DASHBOARD_HOST=127.0.0.1 and do not port-forward it to the public Internet. To view from your main PC when the HP laptop is running the bot, use **Windows OpenSSH tunnel** or a properly authenticated private network, rather than binding the dashboard publicly:

```powershell
# From the MAIN PC, after configuring SSH access on the HP laptop:
ssh -L 8765:127.0.0.1:8765 your_laptop_username@your_laptop_private_ip
```

Then browse http://127.0.0.1:8765/ on the main PC. (You must install/configure OpenSSH Server on the laptop; this command does not do that setup.) The bot keeps running separately on the HP laptop. Avoid exposing the dashboard via router port forwarding.

**Upgrade without affecting the running laptop:** make a copy/backup of `data/signals.sqlite3` and `.env` after stopping the existing bot cleanly, install the updated code, leave your existing `.env` unchanged, reinstall Python requirements, and restart the bot. The SQLite update creates a new outcomes table without dropping existing alert records. Windows quick-launch is still `run_windows.bat`.

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


## ZEUS Black Box V4 — decision-time audit

**New V4 alerts** save a read-only decision evidence record at the moment the
signal qualifies, attached to the existing delivered-alert row. Open the
**Inspect** button in the Command Center's **Signal history** table to review:

- Aggressive taker buy/sell quote volume, percentage and aggregate events.
- EMA 9/21, RSI 14, candle volume ratio, top-20 bid/ask liquidity, spread.
- Every passed/failed directional confirmation (from the *same* strategy
  checks that decide whether to alert), mandatory filters and their thresholds.
- Confirmation-gate configuration and a captured UTC timestamp.

**No change to trading signals, no trading execution, and no confidence score.**
The Black Box is an evidence snapshot, not a measure of signal accuracy.
The current implementation captures *delivered signals*, not every rejected
candidate. It records configured gate requirements, not a tick-by-tick gate audit.

The SQLite `alert_evidence` table is added on startup without deleting
previous alerts. Older V3 alerts show **Not recorded**; old evidence is never
fabricated. Raw JSON is available via read-only
`GET /api/signals/{alert_id}/blackbox` for saved V4 alerts.

**Upgrade safely:** stop the existing bot, back up `.env` and
`data/signals.sqlite3` (including WAL state, preferably after a clean stop),
then deploy V4 into the existing project or a copy. Keep the existing `.env`.
Run `python -m unittest discover -s tests -v`, start only one bot instance,
and confirm the dashboard at `http://127.0.0.1:8765`. Leave the service bound
to localhost and use your existing private Tailscale Serve configuration.
