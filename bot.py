"""Zeus Crypto Signal Bot V2 — live market data, research alerts, dashboard.

Python 3.11+; public Binance Spot data only; never handles exchange credentials.
"""
import argparse
from collections import deque
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
from indicators import Technical, candles_metrics, book_metrics, timeframe_trend
from strategy import evaluate
from blackbox import capture_evidence
from flow_lab import FlowResearch
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
        self.flow_research = {symbol: FlowResearch(symbol, config.large_trade_min_usdt) for symbol in config.symbols}
        self.technical: dict[str, Technical] = {}
        self.store = StateStore(config.data_dir / "signals.sqlite3")
        self.gate = SignalGate(self.store, config.cooldown_seconds, config.confirmations, config.neutral_windows)
        self.connected = False
        self.stop = asyncio.Event()
        self.session: aiohttp.ClientSession | None = None
        self.last_error = ""
        self.started_epoch = time.time()
        self.price_trails = {symbol: deque(maxlen=360) for symbol in config.symbols}
        self.last_prices = {}  # symbol -> (wall-clock UTC seconds, public spot price)
        self.last_market_ok = {}
        self.delivery_success_at = None
        self.delivery_failure_at = None
        self.delivery_success_count = 0
        self.delivery_failure_count = 0

    def dashboard_status(self):
        now_mono = time.monotonic()
        now_epoch = time.time()
        result = {}
        for sym, window in self.windows.items():
            flow = window.snapshot(now_mono) if self.connected else None
            tech = self.technical.get(sym)
            technical_fresh = bool(tech and tech.fresh(now_mono))
            trade_age = round(now_mono - window.last_at, 1) if window.last_at else None
            stream_fresh = bool(self.connected and trade_age is not None and trade_age <= 15)
            market_age = round(now_mono - self.last_market_ok[sym], 1) if sym in self.last_market_ok else None
            result[sym] = {
                **self.flow_research[sym].snapshot(),
                "trend_5m": tech.trend_5m if technical_fresh else "UNKNOWN",
                "trend_15m": tech.trend_15m if technical_fresh else "UNKNOWN",
                "price": window.events[-1][1] if window.events else None,
                "buy_pct": round(flow.buy_pct, 2) if flow else None,
                "sell_pct": round(flow.sell_pct, 2) if flow else None,
                "trades": flow.trades if flow else len(window.events),
                "buy_events": flow.buy_count if flow else None,
                "sell_events": flow.sell_count if flow else None,
                "buy_quote": round(flow.buy_quote, 2) if flow else None,
                "sell_quote": round(flow.sell_quote, 2) if flow else None,
                "price_change_pct": round(flow.price_change_pct, 4) if flow else None,
                "window_covered_seconds": round(flow.window_covered_seconds, 1) if flow else None,
                "trend": ("UP" if tech.ema_fast > tech.ema_slow else "DOWN") if technical_fresh else None,
                "rsi": round(tech.rsi, 2) if technical_fresh else None,
                "ema_fast": round(tech.ema_fast, 8) if technical_fresh else None,
                "ema_slow": round(tech.ema_slow, 8) if technical_fresh else None,
                "volume_ratio": round(tech.volume_ratio, 3) if technical_fresh else None,
                "book_ratio": round(tech.bid_ask_ratio, 3) if technical_fresh else None,
                "spread_bps": round(tech.spread_bps, 3) if technical_fresh else None,
                "last_trade_age_seconds": trade_age,
                "market_data_age_seconds": market_age,
                "trade_fresh": stream_fresh,
                "indicators_fresh": technical_fresh,
                "ready": bool(stream_fresh and technical_fresh and flow),
                "price_series": [{"time": round(ts), "price": price} for ts, price in self.price_trails[sym]],
            }
        ready_count = sum(int(s["ready"]) for s in result.values())
        return {
            "connected": self.connected,
            "healthy": bool(self.connected and ready_count == len(self.cfg.symbols)),
            "ready_markets": ready_count,
            "symbols": result,
            "last_error": self.last_error,
            "time_utc": datetime.now(timezone.utc).isoformat(),
            "system": {
                "started_utc": datetime.fromtimestamp(self.started_epoch, timezone.utc).isoformat(),
                "uptime_seconds": round(now_epoch - self.started_epoch),
                "alert_mode": "CONSOLE" if self.cfg.console_only else "DISCORD",
                "success_count_since_start": self.delivery_success_count,
                "failure_count_since_start": self.delivery_failure_count,
                "last_delivery_success_utc": datetime.fromtimestamp(self.delivery_success_at, timezone.utc).isoformat() if self.delivery_success_at else None,
                "last_delivery_failure_utc": datetime.fromtimestamp(self.delivery_failure_at, timezone.utc).isoformat() if self.delivery_failure_at else None,
                "dashboard_local_only": self.cfg.dashboard_host in ("127.0.0.1", "localhost", "::1"),
                "settings": {
                    "window_seconds": self.cfg.window_seconds,
                    "pressure_threshold_pct": round(self.cfg.pressure_threshold * 100, 1),
                    "min_events": self.cfg.min_trades,
                    "confirmation_checks": self.cfg.min_confirming_factors,
                    "consecutive_evaluations": self.cfg.confirmations,
                    "cooldown_seconds": self.cfg.cooldown_seconds,
                    "evaluation_seconds": self.cfg.evaluation_seconds,
                    "minimum_volume_ratio": self.cfg.min_volume_ratio,
                    "mtf_gate_enabled": self.cfg.mtf_gate_enabled,
                    "large_trade_min_usdt": self.cfg.large_trade_min_usdt,
                    "replay_retention_days": self.cfg.replay_retention_days,
                },
            },
        }

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
            candles, book, five, fifteen = await asyncio.gather(
                self._get("/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 100}),
                self._get("/api/v3/depth", {"symbol": symbol, "limit": 20}),
                self._get("/api/v3/klines", {"symbol": symbol, "interval": "5m", "limit": 100}),
                self._get("/api/v3/klines", {"symbol": symbol, "interval": "15m", "limit": 100}),
                return_exceptions=True,
            )
            # Keep V3 signal monitoring alive even when optional timeframe data fails.
            if isinstance(candles, BaseException):
                raise RuntimeError("1m candles unavailable") from candles
            if isinstance(book, BaseException):
                raise RuntimeError("depth unavailable") from book
            now_ms = int(time.time() * 1000)
            fast, slow, rsi_v, volume, closed_ms = candles_metrics(candles, now_ms)
            ratio, spread = book_metrics(book)
            tick = time.monotonic()
            trend_5m = timeframe_trend(five, now_ms) if isinstance(five, list) else "UNKNOWN"
            trend_15m = timeframe_trend(fifteen, now_ms) if isinstance(fifteen, list) else "UNKNOWN"
            self.technical[symbol] = Technical(fast, slow, rsi_v, volume, ratio, spread, tick, tick, closed_ms,
                                               trend_5m, trend_15m)
            self.last_market_ok[symbol] = tick
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
                    for lab in self.flow_research.values():
                        lab.reset()
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
                                    current_mono, current_epoch = time.monotonic(), time.time()
                                    trade_price = float(data["p"])
                                    accepted = window.add(t=current_mono, price=trade_price, qty=float(data["q"]),
                                                          is_buyer_maker=bool(data["m"]), trade_id=int(data["a"]))
                                    if accepted:
                                        self.flow_research[symbol].on_trade(epoch=current_epoch, price=trade_price, quantity=float(data["q"]), is_buyer_maker=bool(data["m"]))
                                        self.last_prices[symbol] = (current_epoch, trade_price)
                                        series = self.price_trails[symbol]
                                        if not series or current_epoch - series[-1][0] >= 5:
                                            series.append((current_epoch, trade_price))
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
            # Persist observed/missed price follow-through at 5/15/30/60 minutes.
            # No future move is assumed across outages or absent live ticks.
            self.store.update_outcomes(self.last_prices, now_epoch)
            self.store.record_prices(self.last_prices, now_epoch, self.cfg.replay_retention_days)
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
                # Context-only by default; opt-in strict gate via MTF_GATE_ENABLED=true.
                if candidate is not None and self.cfg.mtf_gate_enabled:
                    desired = "UP" if candidate.side == "BUY" else "DOWN"
                    if tech.trend_5m != desired or tech.trend_15m != desired:
                        candidate = None
                if not self.gate.eligible(symbol, candidate.side if candidate else None, now_epoch):
                    continue
                assert candidate is not None and self.session is not None
                # Capture BEFORE delivery; store only if the webhook/console succeeds.
                evidence = capture_evidence(candidate, self.cfg, decision_epoch=now_epoch)
                message = format_alert(candidate)
                if await send_alert(self.session, self.cfg.webhook, message, self.cfg.console_only):
                    delivered_at = time.time()
                    self.gate.sent(candidate, delivered_at, evidence=evidence)
                    self.delivery_success_at = delivered_at
                    self.delivery_success_count += 1
                    log.info("SENT %s %s price=%s pressure=%.1f%% factors=%s",
                             symbol, candidate.side, candidate.flow.price,
                             candidate.flow.buy_pct if candidate.side == "BUY" else candidate.flow.sell_pct,
                             ",".join(candidate.factors))
                else:
                    self.delivery_failure_count += 1
                    self.delivery_failure_at = time.time()
                    self.last_error = "Discord alert delivery failed"
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
                                               self.dashboard_status, self.store.history, self.store)
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
