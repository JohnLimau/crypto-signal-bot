"""ZEUS Black Box V4: read-only, decision-time evidence for delivered signals.

Records exactly what the strategy saw, not future price movement or a probability.
This module never changes the BUY/SELL decision.
"""
from datetime import datetime, timezone

from strategy import Candidate, factor_checks


def capture_evidence(candidate: Candidate, cfg, decision_epoch: float) -> dict:
    """Capture an immutable JSON-compatible snapshot BEFORE webhook delivery.

    The store persists this evidence only if the alert was delivered/printed.
    All checks use the same canonical strategy function, avoiding logic drift.
    """
    f, t = candidate.flow, candidate.technical
    side = candidate.side
    checks = factor_checks(f, t, side)
    pressure = f.buy_pct if side == "BUY" else f.sell_pct
    cutoff_pct = cfg.pressure_threshold * 100
    alignment_pass = (f.price_change_pct >= -0.05 if side == "BUY"
                      else f.price_change_pct <= 0.05)
    rules = [
        {
            "name": "Aggressive taker pressure",
            "passed": pressure >= cutoff_pct,
            "observed": f"{pressure:.2f}% {side}",
            "required": f"at least {cutoff_pct:.2f}% on signal side",
        },
        {
            "name": "Spread filter",
            "passed": t.spread_bps <= 20,
            "observed": f"{t.spread_bps:.3f} bps",
            "required": "at most 20 bps",
        },
        {
            "name": "Closed-candle volume",
            "passed": t.volume_ratio >= cfg.min_volume_ratio,
            "observed": f"{t.volume_ratio:.3f}x previous 20 closed minutes",
            "required": f"at least {cfg.min_volume_ratio:.3f}x",
        },
        {
            "name": "Avoid sharp opposing price move",
            "passed": alignment_pass,
            "observed": f"{f.price_change_pct:+.4f}% over rolling window",
            "required": "BUY: >= -0.05%; SELL: <= +0.05%",
        },
    ]
    direction_checks = [{"name": name, "passed": bool(passed)}
                        for name, passed in checks.items()]
    return {
        "schema_version": 1,
        "captured_at_epoch": decision_epoch,
        "captured_at_utc": datetime.fromtimestamp(decision_epoch, timezone.utc).isoformat(),
        "symbol": candidate.symbol,
        "side": side,
        "reference_price": f.price,
        "source": "Binance public spot aggTrade, closed 1-minute klines, top-20 depth snapshot",
        "order_flow": {
            "window_seconds": f.window_seconds,
            "covered_seconds": f.window_covered_seconds,
            "aggregate_events": f.trades,
            "buy_events": f.buy_count,
            "sell_events": f.sell_count,
            "buy_quote_usdt": f.buy_quote,
            "sell_quote_usdt": f.sell_quote,
            "buy_pressure_pct": f.buy_pct,
            "sell_pressure_pct": f.sell_pct,
            "price_change_pct": f.price_change_pct,
        },
        "technicals": {
            "ema_9": t.ema_fast,
            "ema_21": t.ema_slow,
            "rsi_14": t.rsi,
            "volume_ratio": t.volume_ratio,
            "bid_ask_liquidity_ratio": t.bid_ask_ratio,
            "spread_bps": t.spread_bps,
            "last_closed_candle_utc": datetime.fromtimestamp(
                t.last_closed_ms / 1000, timezone.utc).isoformat(),
        },
        "rules": rules,
        "directional_checks": direction_checks,
        "passed_directional_checks": sum(bool(v) for v in checks.values()),
        "required_directional_checks": cfg.min_confirming_factors,
        "gate": {
            "eligible_at_decision": True,
            "required_consecutive_evaluations": cfg.confirmations,
            "cooldown_seconds": cfg.cooldown_seconds,
            "neutral_evaluations_to_rearm": cfg.neutral_windows,
        },
        "disclaimer": (
            "Decision-time research evidence only. Not an execution record, "
            "success probability, whale identification, or profit guarantee."
        ),
    }
