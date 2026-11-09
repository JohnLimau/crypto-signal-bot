"""SQLite-backed alert history and restart-safe duplicate prevention."""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
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
        self.db.commit()

    def gate(self, symbol: str):
        row = self.db.execute("SELECT last_side, last_sent_at, armed FROM gates WHERE symbol=?", (symbol,)).fetchone()
        return row if row else ("", 0.0, 1)

    def rearm(self, symbol: str):
        last_side, last_at, _ = self.gate(symbol)
        self.db.execute("INSERT OR REPLACE INTO gates VALUES(?,?,?,1)", (symbol, last_side, last_at))
        self.db.commit()

    def mark_sent(self, candidate: Candidate, timestamp: float):
        f = candidate.flow
        with self.db:
            self.db.execute("INSERT INTO alerts(symbol,side,sent_at,price,buy_pct,trades,factors) VALUES (?,?,?,?,?,?,?)",
                            (candidate.symbol, candidate.side, timestamp, f.price, f.buy_pct, f.trades, ", ".join(candidate.factors)))
            self.db.execute("INSERT OR REPLACE INTO gates VALUES(?,?,?,0)",
                            (candidate.symbol, candidate.side, timestamp))

    def history(self, limit: int = 50):
        rows = self.db.execute("SELECT symbol,side,sent_at,price,buy_pct,trades,factors FROM alerts ORDER BY id DESC LIMIT ?", (min(200, limit),)).fetchall()
        return [{"symbol": r[0], "side": r[1], "time": datetime.fromtimestamp(r[2], timezone.utc).isoformat(),
                 "price": r[3], "buy_pct": round(r[4], 1), "trades": r[5], "factors": r[6]} for r in rows]

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

    def sent(self, candidate: Candidate, now_epoch: float):
        self.store.mark_sent(candidate, now_epoch)
        self.pending[candidate.symbol] = ("", 0)
