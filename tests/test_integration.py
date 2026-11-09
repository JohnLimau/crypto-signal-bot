"""Small local end-to-end integration: fake Binance REST + WS -> real strategy -> fake Discord."""
import asyncio
import socket
import tempfile
import time
from pathlib import Path
import unittest

from aiohttp import web
from config import Config
from bot import SignalBot


def unused_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_live_simulated_pipeline(self):
        # No actual exchange or Discord connection: exercise network components end-to-end.
        messages = []
        app = web.Application()

        async def klines(_):
            now = int(time.time()*1000)
            rows = []
            for i in range(60):
                # 60 closed 1m candles, increasing price with RSI near 100;
                # EMA and taker price movement serve as the two confirmations.
                rows.append([now-(61-i)*60000, '100000', '100500', '99500', str(100000+i*2),
                             '50', now-(60-i)*60000-1, '0', 0, '0', '0', '0'])
            return web.json_response(rows)

        async def depth(_):
            return web.json_response({'bids': [['99999','20']], 'asks': [['100000','10']]})

        async def hook(request):
            messages.append(await request.json())
            return web.Response(status=204)

        async def ws_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            i = 0
            try:
                while not ws.closed:
                    i += 1
                    # 2 aggregate events per second, 100% aggressive buys.
                    await ws.send_json({'stream':'btcusdt@aggTrade', 'data':{'e':'aggTrade',
                        's':'BTCUSDT', 'p':str(100000+i*4), 'q':'0.1', 'm':False, 'a':i}})
                    await asyncio.sleep(.5)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            return ws

        app.router.add_get('/api/v3/klines', klines)
        app.router.add_get('/api/v3/depth', depth)
        app.router.add_get('/stream', ws_handler)
        app.router.add_post('/api/webhooks/123/test', hook)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(
                symbols=('BTCUSDT',), webhook=f'http://127.0.0.1:{port}/api/webhooks/123/test',
                console_only=False, window_seconds=10, pressure_threshold=.65, min_trades=5,
                cooldown_seconds=180, evaluation_seconds=1, confirmations=2, neutral_windows=2,
                min_confirming_factors=2, min_volume_ratio=.65,
                rest_url=f'http://127.0.0.1:{port}',
                ws_base=f'ws://127.0.0.1:{port}/stream?streams=',
                data_dir=Path(d), dashboard_host='127.0.0.1', dashboard_port=unused_port())
            bot = SignalBot(cfg)
            task = asyncio.create_task(bot.run())
            try:
                for _ in range(28):
                    if messages:
                        break
                    await asyncio.sleep(.5)
                self.assertEqual(len(messages), 1, 'expected one live simulated signal')
                self.assertIn('BUY SIGNAL', messages[0]['content'])
                self.assertEqual(len(bot.store.history()), 1)
                await asyncio.sleep(1.2)
                self.assertEqual(len(messages), 1, 'repeated unchanged alerts must be blocked')
            finally:
                bot.stop.set()
                await asyncio.wait_for(task, timeout=5)
        await runner.cleanup()


if __name__ == '__main__':
    unittest.main()
