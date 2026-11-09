"""Discord webhook transport with retry and safe mention handling."""
from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import logging
import aiohttp
from strategy import Candidate

log = logging.getLogger(__name__)


def format_alert(c: Candidate) -> str:
    f, t = c.flow, c.technical
    icon = "🟢" if c.side == "BUY" else "🔴"
    when = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).strftime("%d %b %Y, %I:%M:%S %p MYT")
    return (f"{icon} **{c.side} SIGNAL | {c.symbol}**\n"
            f"Price now: **${f.price:,.6f}** (not a guaranteed entry)\n"
            f"Aggressive BUY/SELL: **{f.buy_pct:.1f}% / {f.sell_pct:.1f}%**\n"
            f"Buy/Sell quote volume: **${f.buy_quote:,.0f} / ${f.sell_quote:,.0f}**\n"
            f"Aggregated events: **{f.trades:,}** (B {f.buy_count:,} / S {f.sell_count:,})\n"
            f"{f.window_seconds}s price change: **{f.price_change_pct:+.3f}%**\n"
            f"EMA9 / EMA21: `{t.ema_fast:.4f} / {t.ema_slow:.4f}` | RSI14: `{t.rsi:.1f}`\n"
            f"Volume vs last 20m: `{t.volume_ratio:.2f}x` | Bid/ask liquidity: `{t.bid_ask_ratio:.2f}x`\n"
            f"Confirmations ({len(c.factors)}/4): {', '.join(c.factors)}\n"
            f"Time: {when}\n"
            "*Research alert only. Not financial advice, not an automatic order.*")


async def send_alert(session: aiohttp.ClientSession, webhook: str, content: str, console_only: bool) -> bool:
    if console_only:
        log.info("CONSOLE-ONLY ALERT\n%s", content)
        return True
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    for attempt in range(3):
        try:
            async with session.post(webhook, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (200, 204):
                    return True
                body = (await resp.text())[:240]
                if resp.status == 429:
                    try:
                        retry = float((await resp.json(content_type=None)).get("retry_after", 2))
                    except (ValueError, aiohttp.ContentTypeError):
                        retry = 2.0
                    await asyncio.sleep(max(1, min(retry, 15)))
                    continue
                # 400/401/403/404 won't improve on retry.
                log.error("Discord returned HTTP %s: %s", resp.status, body)
                if resp.status < 500:
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Discord delivery failed (attempt %d/3): %s", attempt+1, type(exc).__name__)
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)
    return False
