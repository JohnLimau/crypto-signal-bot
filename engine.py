"""Incremental rolling taker-flow calculation (no repeated sum over all trades)."""
from collections import deque
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Flow:
    symbol: str
    price: float
    trades: int  # count of Binance aggregate trade EVENTS, not individual fills
    buy_count: int
    sell_count: int
    buy_quote: float
    sell_quote: float
    buy_pct: float
    sell_pct: float
    price_change_pct: float
    window_covered_seconds: float
    window_seconds: int


class TradeWindow:
    def __init__(self, symbol: str, window_seconds: int = 60, min_trades: int = 100):
        self.symbol = symbol.upper()
        self.window_seconds = window_seconds
        self.min_trades = min_trades
        self.reset()

    def reset(self):
        self.events = deque()
        self.buy_quote = 0.0
        self.sell_quote = 0.0
        self.buy_count = 0
        self.sell_count = 0
        self.last_id = -1
        self.last_at = 0.0

    def add(self, *, t: float, price: float, qty: float, is_buyer_maker: bool, trade_id: int) -> bool:
        if not (math.isfinite(price) and price > 0 and math.isfinite(qty) and qty > 0):
            return False
        if trade_id <= self.last_id:
            return False
        self.last_id = trade_id
        # Binance m=true means buyer was passive maker, so the taker SOLD.
        buy = not is_buyer_maker
        quote = price * qty
        self.events.append((t, price, quote, buy))
        if buy:
            self.buy_quote += quote
            self.buy_count += 1
        else:
            self.sell_quote += quote
            self.sell_count += 1
        self.last_at = t
        self._expire(t)
        return True

    def _expire(self, now: float):
        cutoff = now - self.window_seconds
        while self.events and self.events[0][0] < cutoff:
            _, _, quote, buy = self.events.popleft()
            if buy:
                self.buy_quote -= quote
                self.buy_count -= 1
            else:
                self.sell_quote -= quote
                self.sell_count -= 1

    def snapshot(self, now: float) -> Flow | None:
        self._expire(now)
        if (not self.events or len(self.events) < self.min_trades or now - self.last_at > 15):
            return None
        covered = self.events[-1][0] - self.events[0][0]
        if covered < self.window_seconds * 0.80:
            return None  # avoid acting on a partial window after startup/reconnect
        buy, sell = max(0.0, self.buy_quote), max(0.0, self.sell_quote)
        total = buy + sell
        if total <= 0:
            return None
        first_price = self.events[0][1]
        price = self.events[-1][1]
        return Flow(
            symbol=self.symbol, price=price, trades=len(self.events),
            buy_count=self.buy_count, sell_count=self.sell_count,
            buy_quote=buy, sell_quote=sell,
            buy_pct=buy / total * 100, sell_pct=sell / total * 100,
            price_change_pct=(price / first_price - 1) * 100,
            window_covered_seconds=covered, window_seconds=self.window_seconds,
        )
