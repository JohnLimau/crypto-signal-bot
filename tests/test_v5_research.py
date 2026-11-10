"""V5 regression tests: live order-flow, replay persistence and accuracy research."""
import tempfile
import time
import unittest
from pathlib import Path

from flow_lab import FlowResearch
from indicators import timeframe_trend
from state import StateStore


class FlowLabTests(unittest.TestCase):
    def test_cvd_and_large_aggregate_event_not_whale_identity(self):
        lab=FlowResearch("BTCUSDT", 100_000)
        now=time.time()
        lab.on_trade(epoch=now, price=100, quantity=10, is_buyer_maker=False)
        lab.on_trade(epoch=now+5, price=100, quantity=5, is_buyer_maker=True)
        lab.on_trade(epoch=now+10, price=100, quantity=2000, is_buyer_maker=False)
        snap=lab.snapshot()
        self.assertEqual(snap["cvd_usdt"], 200500)
        self.assertEqual(len(snap["large_trade_events"]), 1)
        self.assertEqual(snap["large_trade_events"][0]["side"], "BUY")
        self.assertEqual(snap["cvd_divergence"], "NONE")
        lab.reset()
        self.assertEqual(lab.snapshot()["cvd_usdt"], 0)

    def test_divergence_is_context_not_prediction(self):
        lab=FlowResearch("BTCUSDT")
        now=time.time()
        for i in range(14):
            lab.on_trade(epoch=now+5*i, price=100+(i*.08),
                         quantity=10, is_buyer_maker=True)
        self.assertEqual(lab.snapshot()["cvd_divergence"], "PRICE_UP_CVD_DOWN")

    def test_timeframe_closed_only(self):
        now=int(time.time()*1000)
        rows=[]
        for i in range(38):
            row=[0]*12
            row[4]=str(100+i)
            row[6]=now-((38-i)*60_000)
            rows.append(row)
        self.assertEqual(timeframe_trend(rows,now),"UP")
        self.assertEqual(timeframe_trend(rows[:15],now),"UNKNOWN")


class DataResearchTests(unittest.TestCase):
    def test_recorded_replay_is_real_sampled_data_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"signals.sqlite3"
            now=time.time()
            store=StateStore(path)
            store.record_prices({"BTCUSDT":(now,70000),"ETHUSDT":(now-99,2000)},now)
            store.record_prices({"BTCUSDT":(now+10,70002)},now+10)
            self.assertEqual(store.replay("BTCUSDT",hours=1)["count"],2)
            self.assertEqual(store.replay("ETHUSDT",hours=1)["count"],0)
            store.close()
            reopened=StateStore(path)
            result=reopened.replay("BTCUSDT",hours=1)
            self.assertEqual([x["price"] for x in result["points"]],[70000,70002])
            reopened.close()

    def test_accuracy_lab_keeps_observed_and_missed_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(Path(directory)/"signals.sqlite3")
            now=time.time()
            with store.db:
                for idx in range(3):
                    cur=store.db.execute(
                        "INSERT INTO alerts(symbol,side,sent_at,price,buy_pct,trades,factors) VALUES(?,?,?,?,?,?,?)",
                        ("BTCUSDT","BUY",now-100,100,70,120,"EMA"))
                    store.db.execute(
                        "INSERT INTO signal_outcomes(alert_id,horizon_minutes,status,directional_move_pct) VALUES(?,?,?,?)",
                        (cur.lastrowid,15,"observed" if idx<2 else "missed", 1.0 if idx==0 else (-0.5 if idx==1 else None)))
            groups=store.accuracy_lab(symbol="BTCUSDT",hours=24)["groups"]
            all_15=next(r for r in groups if r["group"]=="ALL" and r["horizon_minutes"]==15)
            self.assertEqual((all_15["observed"],all_15["missed"],all_15["positive"]),(2,1,1))
            self.assertEqual(all_15["positive_pct"],50.0)
            self.assertEqual(all_15["average_move_pct"],0.25)
            self.assertTrue(all_15["sample_warning"])
            store.close()


if __name__=="__main__":
    unittest.main()
