"""Closed-candle trend and top-of-book confirmation."""
from dataclasses import dataclass
import time


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("Insufficient EMA data")
    result = sum(values[:period]) / period
    k = 2.0 / (period + 1)
    for value in values[period:]:
        result = value * k + result * (1 - k)
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError("Insufficient RSI data")
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(0., d) for d in changes]
    losses = [max(0., -d) for d in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + avg_gain / avg_loss)


@dataclass(frozen=True)
class Technical:
    ema_fast: float
    ema_slow: float
    rsi: float
    volume_ratio: float
    bid_ask_ratio: float
    spread_bps: float
    candle_at: float  # monotonic time REST data was fetched
    book_at: float    # monotonic time REST data was fetched
    last_closed_ms: int

    def fresh(self, now: float) -> bool:
        # Reject missing/old market inputs rather than trusting yesterday's indicators.
        candle_age_s = time.time() - self.last_closed_ms / 1000
        return (now - self.candle_at <= 90 and now - self.book_at <= 40
                and -60 <= candle_age_s <= 240)


def candles_metrics(rows: list, server_epoch_ms: int) -> tuple[float, float, float, float, int]:
    closed = [row for row in rows if int(row[6]) < server_epoch_ms]
    if len(closed) < 35:
        raise ValueError("Not enough closed candles")
    prices = [float(row[4]) for row in closed]
    volumes = [float(row[5]) for row in closed]
    prev_20 = volumes[-21:-1]
    baseline = sum(prev_20) / len(prev_20)
    ratio = volumes[-1] / baseline if baseline > 0 else 0.0
    return ema(prices, 9), ema(prices, 21), rsi(prices), ratio, int(closed[-1][6])


def book_metrics(book: dict) -> tuple[float, float]:
    bids = book["bids"]
    asks = book["asks"]
    if not bids or not asks:
        raise ValueError("Empty order book")
    bid_best = float(bids[0][0])
    ask_best = float(asks[0][0])
    if bid_best <= 0 or ask_best <= 0 or ask_best < bid_best:
        raise ValueError("Invalid order book spread")
    bid_quote = sum(float(p) * float(q) for p, q in bids)
    ask_quote = sum(float(p) * float(q) for p, q in asks)
    if ask_quote <= 0:
        raise ValueError("Zero ask liquidity")
    return bid_quote / ask_quote, (ask_best - bid_best) / ((ask_best + bid_best) / 2) * 10000
