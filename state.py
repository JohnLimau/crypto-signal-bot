"""SQLite-backed alert history and restart-safe duplicate prevention."""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import time
from strategy import Candidate


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL,
            sent_at REAL NOT NULL, price REAL NOT NULL, buy_pct REAL NOT NULL,
            trades INTEGER NOT NULL, factors TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS gates (
            symbol TEXT PRIMARY KEY, last_side TEXT NOT NULL,
            last_sent_at REAL NOT NULL, armed INTEGER NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS signal_outcomes (
            alert_id INTEGER NOT NULL REFERENCES alerts(id), horizon_minutes INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('observed', 'missed')),
            sampled_at REAL, sampled_price REAL, directional_move_pct REAL,
            PRIMARY KEY (alert_id, horizon_minutes))""")
        # Additive migration only: legacy alerts remain untouched and have no evidence.
        self.db.execute("""CREATE TABLE IF NOT EXISTS alert_evidence (
            alert_id INTEGER PRIMARY KEY REFERENCES alerts(id),
            captured_at REAL NOT NULL,
            payload_json TEXT NOT NULL)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_sent_at ON alerts(sent_at)")
        self.db.commit()

    def gate(self, symbol: str):
        row = self.db.execute("SELECT last_side, last_sent_at, armed FROM gates WHERE symbol=?", (symbol,)).fetchone()
        return row if row else ("", 0.0, 1)

    def rearm(self, symbol: str):
        last_side, last_at, _ = self.gate(symbol)
        self.db.execute("INSERT OR REPLACE INTO gates VALUES(?,?,?,1)", (symbol, last_side, last_at))
        self.db.commit()

    def mark_sent(self, candidate: Candidate, timestamp: float, evidence: dict | None = None):
        f = candidate.flow
        with self.db:
            cursor = self.db.execute("INSERT INTO alerts(symbol,side,sent_at,price,buy_pct,trades,factors) VALUES (?,?,?,?,?,?,?)",
                                     (candidate.symbol, candidate.side, timestamp, f.price, f.buy_pct, f.trades, ", ".join(candidate.factors)))
            if evidence is not None:
                # Same transaction as alert and gate: never orphan an evidence record.
                self.db.execute("INSERT INTO alert_evidence(alert_id,captured_at,payload_json) VALUES(?,?,?)",
                                (cursor.lastrowid, float(evidence["captured_at_epoch"]),
                                 json.dumps(evidence, allow_nan=False, separators=(",", ":"), sort_keys=True)))
            self.db.execute("INSERT OR REPLACE INTO gates VALUES(?,?,?,0)",
                            (candidate.symbol, candidate.side, timestamp))

    def evidence(self, alert_id: int) -> dict | None:
        """Return a stored decision snapshot, never recompute using current prices."""
        if type(alert_id) is not int or alert_id < 1:
            return None
        row = self.db.execute("SELECT payload_json FROM alert_evidence WHERE alert_id=?",
                              (alert_id,)).fetchone()
        return json.loads(row[0]) if row else None

    HORIZONS = (5, 15, 30, 60)

    def history(self, limit: int = 50, symbol: str = "", side: str = "", hours: int = 0, offset: int = 0):
        """Return saved delivered alerts with observed post-signal spot moves."""
        filters, args = [], []
        if symbol:
            filters.append("symbol = ?")
            args.append(symbol.upper())
        if side:
            filters.append("side = ?")
            args.append(side.upper())
        if hours:
            filters.append("sent_at >= ?")
            args.append(time.time() - hours * 3600)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        limit = min(5000, max(1, int(limit)))
        offset = min(1_000_000, max(0, int(offset)))
        rows = self.db.execute(
            "SELECT id,symbol,side,sent_at,price,buy_pct,trades,factors,EXISTS(SELECT 1 FROM alert_evidence e WHERE e.alert_id=alerts.id) FROM alerts" +
            where + " ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)
        ).fetchall()
        if not rows:
            return []
        ids = [row[0] for row in rows]
        outcomes = self.db.execute(
            f"SELECT alert_id,horizon_minutes,status,sampled_at,sampled_price,directional_move_pct FROM signal_outcomes WHERE alert_id IN ({','.join('?' for _ in ids)})", ids
        ).fetchall()
        result = {row[0]: {} for row in rows}
        for aid, horizon, status, at, price, move in outcomes:
            result[aid][str(horizon)] = {
                "status": status,
                "sampled_at": datetime.fromtimestamp(at, timezone.utc).isoformat() if at else None,
                "sampled_price": price,
                "directional_move_pct": round(move, 4) if move is not None else None
            }
        return [{"id": r[0], "symbol": r[1], "side": r[2],
                 "time": datetime.fromtimestamp(r[3], timezone.utc).isoformat(),
                 "price": r[4], "buy_pct": round(r[5], 1),
                 "trades": r[6], "factors": r[7], "has_evidence": bool(r[8]), "outcomes": result[r[0]]}
                for r in rows]

    def count(self, symbol: str = "", side: str = "", hours: int = 0) -> int:
        filters, args = [], []
        if symbol:
            filters.append("symbol = ?")
            args.append(symbol.upper())
        if side:
            filters.append("side = ?")
            args.append(side.upper())
        if hours:
            filters.append("sent_at >= ?")
            args.append(time.time() - hours * 3600)
        query = "SELECT COUNT(*) FROM alerts"
        if filters:
            query += " WHERE " + " AND ".join(filters)
        return int(self.db.execute(query, args).fetchone()[0])

    def last_alert(self):
        row = self.db.execute("SELECT symbol,side,sent_at,price FROM alerts ORDER BY id DESC LIMIT 1").fetchone()
        return ({"symbol":row[0], "side":row[1],
                 "time":datetime.fromtimestamp(row[2], timezone.utc).isoformat(),
                 "price": row[3]} if row else None)

    def performance(self, hours: int = 168, symbol: str = ""):
        """Observed directional spot moves, NOT executed trade P&L or validated accuracy."""
        since = time.time() - hours * 3600 if hours else 0
        clauses = ["a.sent_at >= ?"]
        base = [since]
        if symbol:
            clauses.append("a.symbol = ?")
            base.append(symbol.upper())
        where = " AND ".join(clauses)
        horizons = {}
        for horizon in self.HORIZONS:
            row = self.db.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN o.status = 'observed' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN o.status = 'missed' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN o.status = 'observed' AND o.directional_move_pct > 0 THEN 1 ELSE 0 END),
                          AVG(CASE WHEN o.status = 'observed' THEN o.directional_move_pct END)
                   FROM alerts a
                   LEFT JOIN signal_outcomes o ON o.alert_id = a.id AND o.horizon_minutes = ?
                   WHERE """ + where, (horizon, *base)
            ).fetchone()
            total, observed, missed, positive, avg = row
            observed, missed, positive = observed or 0, missed or 0, positive or 0
            horizons[str(horizon)] = {
                "total": total, "observed": observed, "missed": missed,
                "pending": max(0, total - observed - missed),
                "positive": positive,
                "positive_rate_pct": round(100 * positive / observed, 1) if observed else None,
                "average_directional_move_pct": round(avg, 4) if avg is not None else None,
            }
        return {"window_hours": hours, "symbol": symbol or "ALL",
                "alert_count": self.count(symbol=symbol, hours=hours),
                "buy_count": self.count(symbol=symbol, side="BUY", hours=hours),
                "sell_count": self.count(symbol=symbol, side="SELL", hours=hours),
                "last_alert": self.last_alert(), "horizons": horizons}

    def update_outcomes(self, latest_prices: dict, now_epoch: float | None = None, max_lag_s: int = 40):
        """Sample the next available live price near each horizon. Do not fabricate missed prices.

        latest_prices maps symbol to (epoch_timestamp, price). Called periodically
        by the event loop, not once per aggTrade event.
        """
        now = time.time() if now_epoch is None else now_epoch
        observed_count = 0
        with self.db:
            for horizon in self.HORIZONS:
                due_before = now - horizon * 60
                rows = self.db.execute(
                    """SELECT a.id,a.symbol,a.side,a.sent_at,a.price FROM alerts a
                       LEFT JOIN signal_outcomes o ON o.alert_id = a.id AND o.horizon_minutes = ?
                       WHERE o.alert_id IS NULL AND a.sent_at <= ?
                       ORDER BY a.id LIMIT 1000""",
                    (horizon, due_before)
                ).fetchall()
                for aid, symbol, side, sent_at, entry in rows:
                    due = sent_at + horizon * 60
                    tick = latest_prices.get(symbol)
                    status, stamp, price, directional = None, None, None, None
                    if tick and due <= tick[0] <= due + max_lag_s and tick[1] > 0:
                        stamp, price = float(tick[0]), float(tick[1])
                        directional = (price / entry - 1) * 100 * (1 if side == "BUY" else -1)
                        status = "observed"
                    elif now > due + max_lag_s:
                        status = "missed"
                    if status:
                        self.db.execute(
                            """INSERT OR IGNORE INTO signal_outcomes
                               (alert_id,horizon_minutes,status,sampled_at,sampled_price,directional_move_pct)
                               VALUES(?,?,?,?,?,?)""",
                            (aid, horizon, status, stamp, price, directional)
                        )
                        observed_count += (status == "observed")
        return observed_count

    def close(self):
        self.db.close()


class SignalGate:
    def __init__(self, store: StateStore, cooldown_seconds: int, confirmations: int, neutral_windows: int):
        self.store = store
        self.cooldown_seconds = cooldown_seconds
        self.confirmations = confirmations
        self.neutral_windows = neutral_windows
        self.pending = defaultdict(lambda: ("", 0))
        self.neutral_streak = defaultdict(int)

    def reset_pending(self):
        self.pending.clear()
        self.neutral_streak.clear()

    def clear_symbol_pending(self, symbol: str):
        self.pending.pop(symbol, None)
        self.neutral_streak.pop(symbol, None)

    def eligible(self, symbol: str, side: str | None, now_epoch: float) -> bool:
        if side is None:
            self.pending[symbol] = ("", 0)
            self.neutral_streak[symbol] += 1
            if self.neutral_streak[symbol] == self.neutral_windows:
                self.store.rearm(symbol)
            return False
        self.neutral_streak[symbol] = 0
        previous, count = self.pending[symbol]
        count = count + 1 if previous == side else 1
        self.pending[symbol] = (side, count)
        last_side, last_time, armed = self.store.gate(symbol)
        return (count >= self.confirmations and now_epoch - last_time >= self.cooldown_seconds
                and (bool(armed) or side != last_side))

    def sent(self, candidate: Candidate, now_epoch: float, evidence: dict | None = None):
        self.store.mark_sent(candidate, now_epoch, evidence=evidence)
        self.pending[candidate.symbol] = ("", 0)
