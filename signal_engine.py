from collections import deque
import time

class SignalEngine:
    """
    Very simple starter engine.

    It measures aggressive buy/sell volume over a rolling window.
    It only emits a signal when:
      - enough trades have accumulated
      - one side exceeds the configured pressure threshold
      - volume is not tiny
      - it is not repeatedly emitting the same side

    This is intentionally NOT presented as a profitable strategy.
    Backtest it before using real money.
    """

    def __init__(
        self,
        symbol,
        window_seconds=60,
        pressure_threshold=65.0,
        min_trades=100,
        cooldown_seconds=300,
    ):
        self.symbol = symbol
        self.window_seconds = window_seconds
        self.pressure_threshold = pressure_threshold
        self.min_trades = min_trades
        self.cooldown_seconds = cooldown_seconds

        self.trades = deque()
        self.last_emitted_side = None
        self.last_emit_time = 0

    def add_trade(self, price, qty, side):
        now = time.time()
        self.trades.append((now, price, qty, side))

        cutoff = now - self.window_seconds
        while self.trades and self.trades[0][0] < cutoff:
            self.trades.popleft()

        if len(self.trades) < self.min_trades:
            return None

        buy_volume = sum(q for _, _, q, s in self.trades if s == "BUY")
        sell_volume = sum(q for _, _, q, s in self.trades if s == "SELL")
        total = buy_volume + sell_volume

        if total <= 0:
            return None

        buy_pct = buy_volume / total * 100
        sell_pct = sell_volume / total * 100
        pressure = buy_pct - sell_pct

        candidate = None
        if buy_pct >= self.pressure_threshold:
            candidate = "BUY"
        elif sell_pct >= self.pressure_threshold:
            candidate = "SELL"

        if not candidate:
            return None

        # Prevent signal spam.
        if candidate == self.last_emitted_side:
            return None

        if now - self.last_emit_time < self.cooldown_seconds:
            return None

        self.last_emitted_side = candidate
        self.last_emit_time = now

        return {
            "symbol": self.symbol,
            "side": candidate,
            "price": price,
            "buy_pct": buy_pct,
            "sell_pct": sell_pct,
            "pressure": pressure,
            "trades": len(self.trades),
        }
