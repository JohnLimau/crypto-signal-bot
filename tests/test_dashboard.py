"""Command Center integration: persisted outcomes, reporting and HTTP UI."""
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import unittest

from aiohttp import ClientSession
from state import StateStore
from dashboard import start_dashboard, safe_csv


def fake_candidate(symbol="BTCUSDT", side="BUY", price=100.0, factors=("EMA",)):
    flow = SimpleNamespace(price=price, buy_pct=72.1, trades=145)
    return SimpleNamespace(symbol=symbol, side=side, flow=flow, factors=factors)


class TestOutcomeTracking(unittest.TestCase):
    def test_real_observation_and_missed_period_are_separate(self):
        with tempfile.TemporaryDirectory() as d:
            db = StateStore(Path(d) / "signals.sqlite3")
            sent_at = time.time() - 2000
            db.mark_sent(fake_candidate(), sent_at)
            # The first 5m observation is within the 40s allowed sampling window.
            db.update_outcomes({"BTCUSDT": (sent_at + 305, 102.0)},
                               now_epoch=sent_at + 310)
            first = db.history(10)[0]
            self.assertEqual(first["outcomes"]["5"]["status"], "observed")
            self.assertAlmostEqual(first["outcomes"]["5"]["directional_move_pct"], 2)
            self.assertNotIn("15", first["outcomes"])
            # After an outage, never pretend the latest price was available at 15m.
            db.update_outcomes({"BTCUSDT": (sent_at + 1010, 104.0)},
                               now_epoch=sent_at + 1010)
            first = db.history(10)[0]
            self.assertEqual(first["outcomes"]["15"]["status"], "missed")
            self.assertEqual(db.performance(hours=24)["horizons"]["5"]["positive_rate_pct"], 100.0)
            self.assertEqual(db.performance(hours=24)["horizons"]["15"]["observed"], 0)
            db.close()
            db2 = StateStore(Path(d) / "signals.sqlite3")
            self.assertEqual(db2.history()[0]["outcomes"]["5"]["status"], "observed")
            db2.close()

    def test_sell_directional_move_and_ineligible_filter(self):
        with tempfile.TemporaryDirectory() as d:
            db = StateStore(Path(d) / "signals.sqlite3")
            sent_at = time.time() - 500
            db.mark_sent(fake_candidate("ETHUSDT", "SELL", price=200), sent_at)
            db.update_outcomes({"ETHUSDT": (sent_at + 308, 196)}, now_epoch=sent_at+310)
            a = db.history(symbol="ETHUSDT", side="SELL")
            self.assertEqual(len(a), 1)
            self.assertAlmostEqual(a[0]["outcomes"]["5"]["directional_move_pct"], 2)
            self.assertEqual(db.count(symbol="BTCUSDT"), 0)
            self.assertEqual(db.performance(hours=24, symbol="ETHUSDT")["horizons"]["5"]["observed"], 1)
            db.close()

    def test_export_formula_escape(self):
        self.assertEqual(safe_csv("=1+2"), "'=1+2")
        self.assertEqual(safe_csv(" normal"), " normal")


class TestDashboardHTTP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "signals.sqlite3")
        self.store.mark_sent(fake_candidate(factors=("=FAKE()",)), time.time()-100)
        self.snapshot = {
            "connected": True, "healthy": True, "ready_markets": 1,
            "symbols": {"BTCUSDT": {"price": 100, "ready": True}},
            "system": {"uptime_seconds": 32},
        }
        self.runner = await start_dashboard("127.0.0.1", 0, lambda:self.snapshot,
                                            self.store.history, self.store,
                                            allow_unauthenticated_test_mode=True)
        site = next(iter(self.runner.sites))
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        self.store.close()
        self.tmp.cleanup()

    async def test_html_assets_api_and_security(self):
        async with self.client.get(self.base+"/") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
            self.assertIn("Market clarity.", await r.text())
        for path, needle in [("/assets/style.css","--canvas"),("/assets/app.js","fetchPerformance")]:
            async with self.client.get(self.base+path) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(needle, await r.text())
        async with self.client.get(self.base+"/api/health") as r:
            self.assertEqual(r.status, 200)
            self.assertTrue((await r.json())["ok"])
        async with self.client.get(self.base+"/api/performance?hours=24") as r:
            self.assertEqual((await r.json())["alert_count"], 1)

    async def test_filtered_history_csv_bad_filters_and_offline_health(self):
        async with self.client.get(self.base+"/api/signals?symbol=BTCUSDT&side=BUY&hours=24") as r:
            data = await r.json()
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["items"][0]["side"], "BUY")
        async with self.client.get(self.base+"/api/signals?symbol=ETHUSDT") as r:
            self.assertEqual(r.status, 400)
        async with self.client.get(self.base+"/api/signals?hours=notanumber") as r:
            self.assertEqual(r.status, 400)
        async with self.client.get(self.base+"/api/export.csv?hours=24") as r:
            self.assertEqual(r.status, 200)
            body = await r.text()
            self.assertIn("5m status", body)
            self.assertIn("'=FAKE()", body)
        self.snapshot["healthy"] = False
        async with self.client.get(self.base+"/api/health") as r:
            self.assertEqual(r.status, 503)


if __name__ == "__main__":
    unittest.main()
