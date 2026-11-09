import asyncio
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from engine import TradeWindow, Flow
from indicators import Technical, book_metrics, candles_metrics, ema, rsi
from strategy import evaluate
from state import StateStore, SignalGate
from config import Config
from notifier import format_alert, send_alert


def make_flow(buy_pct=72, change=0.10):
    return Flow("BTCUSDT", 100000, 180, 115, 65, buy_pct*1000, (100-buy_pct)*1000,
                buy_pct, 100-buy_pct, change, 59, 60)


def make_tech(rsi_value=59, book_ratio=1.14, vol_ratio=1.2):
    now = time.monotonic()
    return Technical(100100, 100000, rsi_value, vol_ratio, book_ratio, 1.5, now, now,
                     int(time.time()*1000)-60000)


class TestFlow(unittest.TestCase):
    def test_buyer_maker_mapping_and_notional(self):
        t = TradeWindow('BTCUSDT', 10, 2)
        self.assertTrue(t.add(t=100, price=100, qty=2, is_buyer_maker=False, trade_id=1))
        self.assertTrue(t.add(t=109, price=100, qty=1, is_buyer_maker=True, trade_id=2))
        f = t.snapshot(109)
        self.assertEqual(f.buy_count, 1)
        self.assertEqual(f.sell_count, 1)
        self.assertAlmostEqual(f.buy_pct, 200/300*100)
        self.assertEqual(f.trades, 2)

    def test_deduplicate_and_expire(self):
        t = TradeWindow('BTCUSDT', 10, 1)
        self.assertTrue(t.add(t=100, price=100, qty=1, is_buyer_maker=False, trade_id=5))
        self.assertFalse(t.add(t=101, price=100, qty=1, is_buyer_maker=True, trade_id=5))
        self.assertTrue(t.add(t=109, price=100, qty=1, is_buyer_maker=False, trade_id=6))
        self.assertEqual(t.snapshot(109).trades, 2)
        self.assertIsNone(t.snapshot(120))

    def test_reset_requires_warmup(self):
        t = TradeWindow('BTCUSDT', 60, 1)
        t.add(t=1, price=100, qty=1, is_buyer_maker=False, trade_id=1)
        t.add(t=20, price=110, qty=1, is_buyer_maker=False, trade_id=2)
        self.assertIsNone(t.snapshot(20))
        t.reset()
        self.assertEqual(len(t.events), 0)


class TestTechnical(unittest.TestCase):
    def test_ema_and_rsi(self):
        self.assertGreater(ema(list(range(1, 50)), 9), ema(list(range(1, 50)), 21))
        self.assertEqual(rsi([5.0]*40), 50)
        self.assertAlmostEqual(rsi(list(range(40))), 100)

    def test_book(self):
        ratio, spread = book_metrics({'bids': [['99','2']], 'asks': [['100','1']]})
        self.assertAlmostEqual(ratio, 1.98)
        self.assertGreater(spread, 0)
        with self.assertRaises(ValueError):
            book_metrics({'bids': [['101','1']], 'asks': [['100','1']]})

    def test_exclude_unclosed_candle(self):
        t0 = int(time.time()*1000)
        rows = []
        for i in range(40):
            rows.append([0, 0, 0, 0, str(100+i), '100', t0-30000 if i == 39 else t0-60000])
        fast, slow, rv, vol, close = candles_metrics(rows, t0-40000)
        self.assertLess(close, t0-40000)
        self.assertLess(fast, 139)
        self.assertEqual(vol, 1)


class TestStrategy(unittest.TestCase):
    def test_buy_candidate(self):
        c = evaluate(make_flow(), make_tech(), now=time.monotonic())
        self.assertIsNotNone(c)
        self.assertEqual(c.side, 'BUY')
        self.assertGreaterEqual(len(c.factors), 2)
        self.assertIn('EMA 9/21 trend', c.factors)

    def test_sell_candidate(self):
        flow = make_flow(22, -0.14)
        t = make_tech(rsi_value=39, book_ratio=0.8)
        t = Technical(99900, 100000, t.rsi, t.volume_ratio, t.bid_ask_ratio, t.spread_bps, t.candle_at, t.book_at, t.last_closed_ms)
        self.assertEqual(evaluate(flow, t, now=time.monotonic()).side, 'SELL')

    def test_no_candidate_under_threshold_or_stale(self):
        self.assertIsNone(evaluate(make_flow(55), make_tech(), now=time.monotonic()))
        self.assertIsNone(evaluate(make_flow(), make_tech(), now=time.monotonic()+999))

    def test_reject_absorption_and_low_volume(self):
        self.assertIsNone(evaluate(make_flow(72, -0.1), make_tech(), now=time.monotonic()))
        self.assertIsNone(evaluate(make_flow(), make_tech(vol_ratio=.1), now=time.monotonic()))

    def test_alert_format(self):
        c = evaluate(make_flow(), make_tech(), now=time.monotonic())
        msg = format_alert(c)
        self.assertIn('BUY SIGNAL', msg)
        self.assertIn('60s price change', msg)
        self.assertIn('MYT', msg)


class TestPersistedGate(unittest.TestCase):
    def test_rearm_dedup_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'x.sqlite3'
            store = StateStore(path)
            c = evaluate(make_flow(), make_tech(), now=time.monotonic())
            gate = SignalGate(store, 180, confirmations=2, neutral_windows=2)
            self.assertFalse(gate.eligible('BTCUSDT', 'BUY', 1000))
            self.assertTrue(gate.eligible('BTCUSDT', 'BUY', 1001))
            gate.sent(c, 1001)
            self.assertEqual(len(store.history()), 1)
            store.close()
            restarted = StateStore(path)
            gate2 = SignalGate(restarted, 180, confirmations=2, neutral_windows=2)
            self.assertFalse(gate2.eligible('BTCUSDT','BUY',1400))
            self.assertFalse(gate2.eligible('BTCUSDT','BUY',1401))
            gate2.clear_symbol_pending('BTCUSDT')  # outage must not rearm
            self.assertEqual(restarted.gate('BTCUSDT')[2], 0)
            gate2.eligible('BTCUSDT',None,1403)
            gate2.eligible('BTCUSDT',None,1404)
            self.assertEqual(restarted.gate('BTCUSDT')[2], 1)
            self.assertFalse(gate2.eligible('BTCUSDT','BUY',1405))
            self.assertTrue(gate2.eligible('BTCUSDT','BUY',1406))
            restarted.close()


class TestConfig(unittest.TestCase):
    def test_invalid_webhook(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': 'https://example.com/hook', 'CONSOLE_ONLY':'false'}):
            with self.assertRaises(ValueError):
                Config.from_env()
        with patch.dict(os.environ, {'DISCORD_WEBHOOK_URL':'', 'CONSOLE_ONLY':'true'}):
            config = Config.from_env()
            self.assertIn('BTCUSDT', config.symbols)


class TestTransport(unittest.IsolatedAsyncioTestCase):
    async def test_console_mode(self):
        self.assertTrue(await send_alert(None, '', 'test signal', True))

    async def test_local_fake_discord_transport(self):
        from aiohttp import web, ClientSession
        captured = []
        async def receive(request):
            captured.append(await request.json())
            return web.Response(status=204)
        app = web.Application()
        app.router.add_post('/hook', receive)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner,'127.0.0.1',0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as s:
                self.assertTrue(await send_alert(s,f'http://127.0.0.1:{port}/hook','Hello world',False))
            self.assertEqual(captured[0]['content'],'Hello world')
            self.assertEqual(captured[0]['allowed_mentions'], {'parse': []})
        finally:
            await runner.cleanup()


if __name__ == '__main__':
    unittest.main()
