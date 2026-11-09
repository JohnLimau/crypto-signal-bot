"""Rule-based alert candidates. The confirmation score is NOT a success probability."""
from dataclasses import dataclass
from engine import Flow
from indicators import Technical


@dataclass(frozen=True)
class Candidate:
    symbol: str
    side: str
    flow: Flow
    technical: Technical
    factors: tuple[str, ...]


def evaluate(flow: Flow | None, tech: Technical | None, *, now: float,
             pressure_threshold: float = .65, min_volume_ratio: float = .65,
             min_factors: int = 2) -> Candidate | None:
    if flow is None or tech is None or not tech.fresh(now):
        return None
    if tech.spread_bps > 20 or tech.volume_ratio < min_volume_ratio:
        return None
    side = "BUY" if flow.buy_pct >= pressure_threshold * 100 else ("SELL" if flow.sell_pct >= pressure_threshold * 100 else None)
    if side is None:
        return None
    # Hard reject obvious absorption against the predicted direction.
    if side == "BUY" and flow.price_change_pct < -0.05:
        return None
    if side == "SELL" and flow.price_change_pct > 0.05:
        return None
    checks = {
        "EMA 9/21 trend": (tech.ema_fast > tech.ema_slow if side == "BUY" else tech.ema_fast < tech.ema_slow),
        "RSI momentum": (50 <= tech.rsi <= 78 if side == "BUY" else 22 <= tech.rsi <= 50),
        "order-book liquidity": (tech.bid_ask_ratio >= 1.07 if side == "BUY" else tech.bid_ask_ratio <= 1 / 1.07),
        "price move": (flow.price_change_pct >= 0.025 if side == "BUY" else flow.price_change_pct <= -0.025),
    }
    factors = tuple(name for name, passed in checks.items() if passed)
    if len(factors) < min_factors:
        return None
    return Candidate(flow.symbol, side, flow, tech, factors)
