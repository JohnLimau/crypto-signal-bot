"""Session-scoped order-flow research, NOT whale identity or predictive certainty."""
from collections import deque
from statistics import median


class FlowResearch:
    """Small bounded in-memory diagnostics for public Binance aggTrade events."""

    def __init__(self, symbol: str, large_trade_min_usdt: float = 100_000):
        self.symbol = symbol
        self.large_trade_min_usdt = large_trade_min_usdt
        self.reset()

    def reset(self):
        self.cvd = 0.0
        self.last_sample_at = 0.0
        self.samples = deque(maxlen=720)
        self.recent_sizes = deque(maxlen=300)
        self.large_trades = deque(maxlen=30)
        self.last_event_at = 0.0

    def on_trade(self, *, epoch: float, price: float, quantity: float, is_buyer_maker: bool):
        """Call only for validated, deduplicated aggregate trade events."""
        quote = price * quantity
        if not 0 < quote < float("inf"):
            return
        buy = not is_buyer_maker
        self.cvd += quote if buy else -quote
        # A large aggregate event is not proof of a single trader or a whale.
        # Computing a median for every live event is expensive. Most events are
        # below the hard floor, so only calculate the relative threshold for
        # events that could possibly qualify.
        threshold = self.large_trade_min_usdt
        if quote >= self.large_trade_min_usdt and len(self.recent_sizes) >= 25:
            threshold = max(threshold, 12 * median(self.recent_sizes))
        if quote >= threshold:
            self.large_trades.appendleft({
                "time": round(epoch, 3), "side": "BUY" if buy else "SELL",
                "quote_usdt": round(quote, 2), "price": price,
                "threshold_usdt": round(threshold, 2),
            })
        self.recent_sizes.append(quote)
        self.last_event_at = epoch
        if epoch - self.last_sample_at >= 5.0:
            self.samples.append({"time": round(epoch, 3), "price": price,
                                 "cvd_usdt": round(self.cvd, 2)})
            self.last_sample_at = epoch

    def snapshot(self):
        recent = list(self.samples)
        divergence = "NONE"
        if len(recent) >= 7:
            last = recent[-1]
            earlier = next((s for s in reversed(recent[:-1])
                            if last["time"] - s["time"] >= 50), None)
            if earlier and earlier["price"] > 0:
                change = 100 * (last["price"] / earlier["price"] - 1)
                cvd_change = last["cvd_usdt"] - earlier["cvd_usdt"]
                if change > 0.05 and cvd_change < 0:
                    divergence = "PRICE_UP_CVD_DOWN"
                elif change < -0.05 and cvd_change > 0:
                    divergence = "PRICE_DOWN_CVD_UP"
        return {
            "cvd_usdt": round(self.cvd, 2),
            "cvd_series": recent[-240:],
            "cvd_divergence": divergence,
            "large_trade_events": list(self.large_trades)[:15],
            "large_trade_threshold_usdt": self.large_trade_min_usdt,
            "session_only": True,
        }
