"""Regression tests for ZEUS Black Box V4: decision evidence and V3 migration."""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from blackbox import capture_evidence
from engine import Flow
from indicators import Technical
from state import StateStore
from strategy import evaluate, factor_checks


class BlackBoxTests(unittest.TestCase):
    def make_candidate(self, side="BUY"):
        buy = side == "BUY"
        now = time.monotonic()
        flow = Flow(
            symbol="BTCUSDT", price=100.0, trades=130,
            buy_count=100 if buy else 30,
            sell_count=30 if buy else 100,
            buy_quote=72000.0 if buy else 28000.0,
            sell_quote=28000.0 if buy else 72000.0,
            buy_pct=72.0 if buy else 28.0,
            sell_pct=28.0 if buy else 72.0,
            price_change_pct=0.06 if buy else -0.06,
            window_covered_seconds=59.0, window_seconds=60
        )
        tech = Technical(
            ema_fast=101.0 if buy else 99.0,
            ema_slow=100.0, rsi=60.0 if buy else 40.0,
            volume_ratio=1.3, bid_ask_ratio=1.2 if buy else 0.8,
            spread_bps=3.0, candle_at=now, book_at=now,
            last_closed_ms=int((time.time() - 60) * 1000)
        )
        candidate = evaluate(flow, tech, now=now)
        self.assertIsNotNone(candidate)
        return candidate

    def make_config(self):
        return SimpleNamespace(
            pressure_threshold=0.65, min_volume_ratio=0.65,
            min_confirming_factors=2, confirmations=2,
            cooldown_seconds=180, neutral_windows=2
        )

    def test_buy_and_sell_evidence_matches_canonical_strategy_checks(self):
        for side in ("BUY", "SELL"):
            with self.subTest(side=side):
                candidate = self.make_candidate(side)
                evidence = capture_evidence(candidate, self.make_config(), time.time())
                checks = factor_checks(candidate.flow, candidate.technical, candidate.side)
                self.assertEqual(evidence["side"], side)
                self.assertEqual(evidence["passed_directional_checks"], sum(checks.values()))
                self.assertEqual(
                    {item["name"]: item["passed"] for item in evidence["directional_checks"]},
                    checks
                )
                self.assertTrue(all(item["passed"] for item in evidence["rules"]))
                self.assertEqual(evidence["order_flow"]["aggregate_events"], 130)

    def test_persists_snapshot_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.sqlite3"
            candidate = self.make_candidate()
            snapshot = capture_evidence(candidate, self.make_config(), time.time())
            store = StateStore(path)
            store.mark_sent(candidate, time.time(), evidence=snapshot)
            alert = store.history()[0]
            self.assertTrue(alert["has_evidence"])
            alert_id = alert["id"]
            store.close()
            reloaded = StateStore(path)
            self.assertEqual(reloaded.evidence(alert_id), snapshot)
            reloaded.close()

    def test_legacy_alerts_remain_without_fabricated_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.sqlite3"
            db = sqlite3.connect(path)
            db.execute("""CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
                side TEXT NOT NULL, sent_at REAL NOT NULL, price REAL NOT NULL,
                buy_pct REAL NOT NULL, trades INTEGER NOT NULL, factors TEXT NOT NULL)""")
            db.execute("INSERT INTO alerts(symbol,side,sent_at,price,buy_pct,trades,factors) VALUES (?,?,?,?,?,?,?)",
                       ("BTCUSDT", "BUY", time.time(), 100, 72, 130, "EMA 9/21 trend"))
            db.commit()
            db.close()
            store = StateStore(path)
            alert = store.history()[0]
            self.assertFalse(alert["has_evidence"])
            self.assertIsNone(store.evidence(alert["id"]))
            store.close()


if __name__ == "__main__":
    unittest.main()
