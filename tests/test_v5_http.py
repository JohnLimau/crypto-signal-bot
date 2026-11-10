"""Basic V5 HTTP smoke tests: assets, read-only APIs and invalid input."""
import socket
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession

from dashboard import start_dashboard
from state import StateStore


class DashboardV5SmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=StateStore(Path(self.temp.name)/"signals.sqlite3")
        self.market_status=lambda:{
            "connected": False, "healthy":False, "ready_markets":0,
            "symbols":{"BTCUSDT":{}}, "time_utc":"2026-11-10T00:00:00+00:00"}
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1",0))
            self.port=sock.getsockname()[1]
        self.runner=await start_dashboard("127.0.0.1",self.port,
                                         self.market_status,self.store.history,self.store)
        self.session=ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()
        self.store.close()
        self.temp.cleanup()

    async def get(self,path):
        return await self.session.get(f"http://127.0.0.1:{self.port}{path}")

    async def test_assets_and_existing_dashboard_work(self):
        for path in ("/", "/assets/style.css", "/assets/app.js",
                     "/assets/themes.css", "/assets/research.js", "/api/status",
                     "/api/signals", "/api/performance"):
            with self.subTest(path=path):
                async with await self.get(path) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.headers.get("X-Content-Type-Options"),"nosniff")

    async def test_new_read_only_research_endpoints(self):
        async with await self.get("/api/research/accuracy?symbol=BTCUSDT&hours=24") as resp:
            self.assertEqual(resp.status,200)
            data=await resp.json()
            self.assertEqual(data["groups"],[])
        async with await self.get("/api/research/replay?symbol=BTCUSDT&hours=1") as resp:
            self.assertEqual(resp.status,200)
            data=await resp.json()
            self.assertEqual(data["points"],[])
        async with await self.get("/api/research/replay?symbol=BADUSDT&hours=1") as resp:
            self.assertEqual(resp.status,400)
        async with await self.get("/api/research/replay?symbol=BTCUSDT&hours=1000") as resp:
            self.assertEqual(resp.status,400)
        async with await self.get("/api/signals/1/blackbox") as resp:
            self.assertEqual(resp.status,404)


if __name__=="__main__":
    unittest.main()
