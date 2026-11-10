"""SQLite-backed alert history and restart-safe duplicate prevention."""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from statistics import median
from math import sqrt
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
        self.db.execute("""CREATE TABLE IF NOT EXISTS market_samples (
            symbol TEXT NOT NULL, sampled_at REAL NOT NULL, price REAL NOT NULL,
            PRIMARY KEY(symbol,sampled_at))""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_market_samples_time ON market_samples(sampled_at)")
        self._last_sample_prune_at = 0.0
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


    def record_prices(self, latest_prices: dict, now_epoch: float,
                      retention_days: int = 14) -> int:
        """Record only recent received spot ticks for future honest market replay."""
        records = [(symbol, float(tick[0]), float(tick[1]))
                   for symbol, tick in latest_prices.items()
                   if (0 <= now_epoch - float(tick[0]) <= 15 and float(tick[1]) > 0)]
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO market_samples(symbol,sampled_at,price) VALUES(?,?,?)",
                records)
            if now_epoch - self._last_sample_prune_at > 3600:
                self.db.execute("DELETE FROM market_samples WHERE sampled_at < ?",
                                (now_epoch - retention_days * 86400,))
                self._last_sample_prune_at = now_epoch
        return len(records)

    def replay(self, symbol: str, hours: int = 1, limit: int = 10000) -> dict:
        """Return stored live spot observations; unavailable history is never invented."""
        if hours not in (1, 6, 24) or not 2 <= limit <= 10000:
            raise ValueError("Invalid replay window")
        since = time.time() - 3600 * hours
        rows = self.db.execute(
            """SELECT sampled_at,price FROM market_samples
               WHERE symbol=? AND sampled_at>=?
               ORDER BY sampled_at DESC LIMIT ?""",
            (symbol, since, limit)).fetchall()
        points = [{"time": r[0], "price": r[1]} for r in reversed(rows)]
        return {"symbol": symbol, "hours": hours, "points": points,
                "count": len(points), "source": "recorded_public_spot_ticks",
                "sampling": "bot loop, usually 10s", "complete_history": False}

    def accuracy_lab(self, symbol: str = "", hours: int = 168) -> dict:
        """Observed direction only; explicit sample sizes and no claim of net profit."""
        if hours not in (0, 24, 72, 168, 720):
            raise ValueError("Invalid research window")
        since = time.time() - hours * 3600 if hours else 0
        clauses = ["a.sent_at >= ?"]
        args = [since]
        if symbol:
            clauses.append("a.symbol = ?")
            args.append(symbol.upper())
        rows = self.db.execute(
            """SELECT a.symbol,a.side,o.horizon_minutes,o.status,o.directional_move_pct
               FROM alerts a LEFT JOIN signal_outcomes o ON a.id=o.alert_id
               WHERE """ + " AND ".join(clauses), args).fetchall()
        groups = {}
        for sym, side, horizon, status, move in rows:
            if horizon not in (5, 15, 60):
                continue
            for key in (("ALL", horizon), (sym, horizon), (side, horizon)):
                group = groups.setdefault(key, {"observed": [], "missed": 0})
                if status == "observed" and move is not None:
                    group["observed"].append(float(move))
                elif status == "missed":
                    group["missed"] += 1
        results = []
        for (group_name, horizon), group in sorted(groups.items()):
            moves = group["observed"]
            n = len(moves)
            wins = sum(x > 0 for x in moves)
            p = wins / n if n else 0.0
            # Wilson interval is uncertainty of the observed positive fraction,
            # not a guarantee of future trading performance.
            if n:
                z = 1.96
                denominator = 1 + z*z/n
                centre = (p + z*z/(2*n))/denominator
                margin = z*sqrt(p*(1-p)/n + z*z/(4*n*n))/denominator
                bounds = [round(max(0, (centre-margin)*100), 1),
                          round(min(100, (centre+margin)*100), 1)]
            else:
                bounds = None
            results.append({
                "group": group_name, "horizon_minutes": horizon,
                "observed": n, "missed": group["missed"],
                "positive": wins, "positive_pct": round(100*p, 1) if n else None,
                "wilson_95_pct": bounds,
                "average_move_pct": round(sum(moves)/n, 4) if n else None,
                "median_move_pct": round(median(moves), 4) if n else None,
                "best_pct": round(max(moves), 4) if n else None,
                "worst_pct": round(min(moves), 4) if n else None,
                "sample_warning": n < 30,
            })
        return {"hours": hours, "symbol": symbol or "ALL", "groups": results,
                "disclaimer": "Observed post-alert spot moves, not executed P&L or verified predictive accuracy. Historical data may be missed or pending."}

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
