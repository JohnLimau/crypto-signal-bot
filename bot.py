"""Zeus Crypto Signal Bot V2 — live market data, research alerts, dashboard.

Python 3.11+; public Binance Spot data only; never handles exchange credentials.
"""
import argparse
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import time
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp

from config import Config
from engine import TradeWindow
from indicators import Technical, candles_metrics, book_metrics
from strategy import evaluate
from state import SignalGate, StateStore
from notifier import send_alert, format_alert
from dashboard import start_dashboard

log = logging.getLogger("signalbot")


def configure_logging(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
        fh = RotatingFileHandler(data_dir / "signalbot.log", maxBytes=3_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


class SignalBot:
    def __init__(self, config: Config):
        self.cfg = config
        self.windows = {symbol: TradeWindow(symbol, config.window_seconds, config.min_trades) for symbol in config.symbols}
        self.metrics = {symbol: {} for symbol in config.symbols}
        self.technical: dict[str, Technical] = {}
        self.store = StateStore(config.data_dir / "signals.sqlite3")
        self.gate = SignalGate(self.store, config.cooldown_seconds, config.confirmations, config.neutral_windows)
        self.connected = False
        self.stop = asyncio.Event()
        self.session: aiohttp.ClientSession | None = None
        self.last_error = ""

    def dashboard_status(self):
        now = time.monotonic()
        result = {}
        for sym, w in self.windows.items():
            f = w.snapshot(now)
            t = self.technical.get(sym)
            result[sym] = {"price": w.events[-1][1] if w.events else None,
                           "buy_pct": round(f.buy_pct, 2) if f else None,
                           "trades": f.trades if f else len(w.events),
                           "trend": ("UP" if t.ema_fast > t.ema_slow else "DOWN") if t and t.fresh(now) else None,
                           "rsi": t.rsi if t and t.fresh(now) else None}
        return {"connected": self.connected, "symbols": result, "last_error": self.last_error,
                "time_utc": datetime.now(timezone.utc).isoformat()}

    async def _get(self, path: str, params: dict):
        assert self.session is not None
        url = self.cfg.rest_url + path
        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Binance REST status={resp.status}, path={path}")
            return await resp.json(content_type=None)

    async def fetch_market(self, symbol: str):
        try:
            # Closed 1-minute candles; incomplete current candle excluded in indicators.py
            candles, book = await asyncio.gather(
                self._get("/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 100}),
                self._get("/api/v3/depth", {"symbol": symbol, "limit": 20}),
            )
            now_ms = int(time.time() * 1000)
            fast, slow, rsi_v, volume, closed_ms = candles_metrics(candles, now_ms)
            ratio, spread = book_metrics(book)
            tick = time.monotonic()
            self.technical[symbol] = Technical(fast, slow, rsi_v, volume, ratio, spread, tick, tick, closed_ms)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            self.last_error = f"Market fetch {symbol}: {exc}"
            log.warning("%s", self.last_error)

    async def refresh_forever(self):
        while not self.stop.is_set():
            await asyncio.gather(*(self.fetch_market(symbol) for symbol in self.cfg.symbols))
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass

    async def stream_forever(self):
        backoff = 2
        streams = "/".join(f"{sym.lower()}@aggTrade" for sym in self.cfg.symbols)
        url = self.cfg.ws_base + quote(streams, safe="/@")
        while not self.stop.is_set():
            try:
                assert self.session is not None
                log.info("Connecting Binance aggregate trades for %s", ", ".join(self.cfg.symbols))
                async with self.session.ws_connect(url, heartbeat=25, receive_timeout=100, autoclose=True, max_msg_size=2_000_000) as ws:
                    # Do NOT combine old and new rolling windows across stream outages.
                    for window in self.windows.values():
                        window.reset()
                    self.gate.reset_pending()
                    self.connected = True
                    self.last_error = ""
                    backoff = 2
                    async for item in ws:
                        if item.type == aiohttp.WSMsgType.TEXT:
                            try:
                                outer = json.loads(item.data)
                                data = outer.get("data", outer)
                                if data.get("e") != "aggTrade":
                                    continue
                                symbol = str(data["s"]).upper()
                                window = self.windows.get(symbol)
                                if window is not None:
                                    window.add(t=time.monotonic(), price=float(data["p"]), qty=float(data["q"]),
                                               is_buyer_maker=bool(data["m"]), trade_id=int(data["a"]))
                            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                                log.debug("Ignored malformed exchange payload")
                        elif item.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, RuntimeError) as exc:
                self.last_error = f"WebSocket: {type(exc).__name__}"
                log.warning("Stream disconnected: %s; retrying", type(exc).__name__)
            finally:
                self.connected = False
                for window in self.windows.values():
                    window.reset()
                self.gate.reset_pending()
            if not self.stop.is_set():
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60)

    async def evaluate_forever(self):
        while not self.stop.is_set():
            now_mono, now_epoch = time.monotonic(), time.time()
            for symbol, window in self.windows.items():
                flow = window.snapshot(now_mono) if self.connected else None
                tech = self.technical.get(symbol)
                # Missing/stale exchange data is NOT a neutral market signal.
                # Never re-arm duplicate alerts because a connection went down.
                if flow is None or tech is None or not tech.fresh(now_mono):
                    self.gate.clear_symbol_pending(symbol)
                    continue
                candidate = evaluate(flow, tech, now=now_mono,
                                     pressure_threshold=self.cfg.pressure_threshold,
                                     min_volume_ratio=self.cfg.min_volume_ratio,
                                     min_factors=self.cfg.min_confirming_factors)
                if not self.gate.eligible(symbol, candidate.side if candidate else None, now_epoch):
                    continue
                assert candidate is not None and self.session is not None
                message = format_alert(candidate)
                if await send_alert(self.session, self.cfg.webhook, message, self.cfg.console_only):
                    self.gate.sent(candidate, time.time())
                    log.info("SENT %s %s price=%s pressure=%.1f%% factors=%s",
                             symbol, candidate.side, candidate.flow.price,
                             candidate.flow.buy_pct if candidate.side == "BUY" else candidate.flow.sell_pct,
                             ",".join(candidate.factors))
                else:
                    log.error("Signal %s %s not delivered; will NOT mark as sent", symbol, candidate.side)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.cfg.evaluation_seconds)
            except asyncio.TimeoutError:
                pass

    async def run(self):
        runner = None
        tasks = []
        try:
            timeout = aiohttp.ClientTimeout(total=15, sock_connect=10)
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "ZeusSignalBot/2.0"}) as session:
                self.session = session
                runner = await start_dashboard(self.cfg.dashboard_host, self.cfg.dashboard_port,
                                               self.dashboard_status, self.store.history)
                log.info("Dashboard: http://%s:%d", self.cfg.dashboard_host, self.cfg.dashboard_port)
                log.info("Alerts: %s | Symbols: %s", "CONSOLE ONLY" if self.cfg.console_only else "DISCORD", ", ".join(self.cfg.symbols))
                tasks = [asyncio.create_task(self.refresh_forever()), asyncio.create_task(self.stream_forever()), asyncio.create_task(self.evaluate_forever())]
                await self.stop.wait()
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if runner:
                await runner.cleanup()
            self.store.close()


async def preflight(config: Config, test_alert: bool = False) -> bool:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        try:
            async with session.get(config.rest_url + "/api/v3/ping") as response:
                if response.status != 200:
                    print(f"Binance public API failed: HTTP {response.status}")
                    return False
            print("OK: Binance public API is reachable")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"FAILED: Binance public API ({type(exc).__name__})")
            return False
        if test_alert:
            ok = await send_alert(session, config.webhook,
                                  "✅ Zeus Signal Bot V2 test message. Delivery check only; not a trade signal.", config.console_only)
            print("OK: alert sent" if ok else "FAILED: Discord test message was not delivered")
            return ok
    return True


def main():
    parser = argparse.ArgumentParser(description="Zeus Crypto Signal Bot V2")
    parser.add_argument("--check", action="store_true", help="Check Binance REST connectivity and configuration")
    parser.add_argument("--test-alert", action="store_true", help="Send one clearly labelled Discord test message")
    args = parser.parse_args()
    try:
        conf = Config.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    configure_logging(conf.data_dir)
    if args.check or args.test_alert:
        raise SystemExit(0 if asyncio.run(preflight(conf, args.test_alert)) else 1)
    bot = SignalBot(conf)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Stopped by user")


if __name__ == "__main__":
    main()
