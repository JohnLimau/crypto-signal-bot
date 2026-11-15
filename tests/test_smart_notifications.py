"""ZEUS V5.2 notification regressions: keep every gated research signal."""
import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import SignalBot
from config import Config
from engine import Flow
from indicators import Technical
from state import StateStore, SignalGate


def candidate(side="BUY", factors=("EMA 9/21 trend", "RSI momentum")):
    flow = SimpleNamespace(price=100.0, buy_pct=72.0, trades=120)
    return SimpleNamespace(symbol="BTCUSDT", side=side, flow=flow, factors=factors)


def evidence(timestamp):
    return {"captured_at_epoch": timestamp, "schema_version": 1,
            "source": "test decision snapshot"}


class NotificationStorageTests(unittest.TestCase):
    def test_legacy_migration_preserves_delivered_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.sqlite3"
            db = sqlite3.connect(path)
            db.execute("""CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL,
                sent_at REAL NOT NULL, price REAL NOT NULL, buy_pct REAL NOT NULL,
                trades INTEGER NOT NULL, factors TEXT NOT NULL)""")
            db.execute("INSERT INTO alerts(symbol,side,sent_at,price,buy_pct,trades,factors) VALUES(?,?,?,?,?,?,?)",
                       ("BTCUSDT","BUY",time.time()-20,100,72,120,"EMA"))
            db.commit()
            db.close()
            store=StateStore(path)
            item=store.history()[0]
            self.assertEqual(item["discord_status"],"delivered")
            self.assertEqual(store.discord_counts(hours=0)["delivered"],1)
            store.close()

    def test_suppressed_signal_still_has_evidence_and_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(Path(directory)/"signals.sqlite3")
            when=time.time()-420
            gate=SignalGate(store,180,1,2)
            c=candidate()
            self.assertTrue(gate.eligible("BTCUSDT","BUY",when))
            alert_id=gate.record(c,when,evidence(when),"Below Discord confirmation threshold")
            saved=store.history()[0]
            self.assertEqual(saved["id"],alert_id)
            self.assertEqual(saved["discord_status"],"quiet")
            self.assertTrue(saved["has_evidence"])
            self.assertIsNotNone(store.evidence(alert_id))
            self.assertEqual(store.discord_counts(hours=0)["quiet"],1)
            # The existing outcome engine keeps tracking the quiet research row.
            store.update_outcomes({"BTCUSDT":(when+305,102)},now_epoch=when+320)
            saved=store.history()[0]
            self.assertEqual(saved["outcomes"]["5"]["status"],"observed")
            self.assertAlmostEqual(saved["outcomes"]["5"]["directional_move_pct"],2.0)
            store.close()

    def test_cooldown_is_persisted_even_after_failed_webhook(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"signals.sqlite3"
            store=StateStore(path)
            now=time.time()
            idx=store.record_candidate(candidate(),now,evidence(now),"Eligible for Discord")
            self.assertTrue(store.discord_cooldown_ok("BTCUSDT",now,1800))
            store.begin_discord_attempt(idx,"BTCUSDT",now)
            store.finish_discord_attempt(idx,False)
            self.assertEqual(store.history()[0]["discord_status"],"failed")
            store.close()
            reopened=StateStore(path)
            self.assertFalse(reopened.discord_cooldown_ok("BTCUSDT",now+100,1800))
            self.assertTrue(reopened.discord_cooldown_ok("BTCUSDT",now+1801,1800))
            reopened.close()

    def test_digest_is_off_by_default_and_claimed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(Path(directory)/"signals.sqlite3")
            base=time.time()-4000
            self.assertIsNone(store.claim_quiet_digest(base,0))
            self.assertIsNone(store.claim_quiet_digest(base,1))
            store.record_candidate(candidate(),base+10,evidence(base+10),"Discord cooldown active")
            summary=store.claim_quiet_digest(base+3601,1)
            self.assertEqual(summary["groups"],[{"symbol":"BTCUSDT","side":"BUY","count":1}])
            self.assertIsNone(store.claim_quiet_digest(base+3602,1))
            store.close()


class NotificationBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_of_four_records_quiet_without_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=Config(
                symbols=("BTCUSDT",),webhook="",console_only=True,
                window_seconds=60,pressure_threshold=.65,min_trades=100,
                cooldown_seconds=180,evaluation_seconds=1,confirmations=1,
                neutral_windows=2,min_confirming_factors=2,min_volume_ratio=.65,
                rest_url="https://data-api.binance.vision",
                ws_base="wss://data-stream.binance.vision/stream?streams=",
                data_dir=Path(directory),dashboard_host="127.0.0.1",
                dashboard_port=8765,discord_cooldown_seconds=1800,
                discord_min_factors=3
            )
            bot=SignalBot(cfg)
            flow=Flow("BTCUSDT",100,120,90,30,72000,28000,72,28,0.0,59,60)
            now=time.monotonic()
            tech=Technical(101,100,65,1.2,0.9,2.0,now,now,
                           int((time.time()-60)*1000))
            bot.windows["BTCUSDT"]=SimpleNamespace(snapshot=lambda _now:flow)
            bot.technical["BTCUSDT"]=tech
            bot.connected=True
            bot.session=object()
            with patch("bot.send_alert",new_callable=AsyncMock) as transport:
                task=asyncio.create_task(bot.evaluate_forever())
                await asyncio.sleep(.1)
                bot.stop.set()
                await asyncio.wait_for(task,timeout=2)
                transport.assert_not_awaited()
            rows=bot.store.history()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["discord_status"],"quiet")
            self.assertEqual(bot.research_recorded_since_start,1)
            bot.store.close()


if __name__=="__main__":
    unittest.main()
