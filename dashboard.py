"""ZEUS Command Center: local-only analytics dashboard with read-only APIs."""
import csv
import io
import re
from datetime import datetime, timezone
from pathlib import Path
from aiohttp import web

ROOT = Path(__file__).resolve().parent / "dashboard_ui"
SECURITY = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
        "base-uri 'none'; form-action 'none'"
    ),
}

def filters(request, status_fn, exporting=False):
    q = request.query
    symbol = q.get("symbol", "").strip().upper()
    if symbol and (not re.fullmatch(r"[A-Z0-9]{5,20}", symbol) or symbol not in status_fn().get("symbols", {})):
        raise web.HTTPBadRequest(text="Unknown symbol")
    side = q.get("side", "").strip().upper()
    if side not in ("", "BUY", "SELL"):
        raise web.HTTPBadRequest(text="Invalid direction")
    try:
        hours, limit, offset = (int(q.get("hours", "168")),
                                int(q.get("limit", "5000" if exporting else "40")),
                                int(q.get("offset", "0")))
    except ValueError as exc:
        raise web.HTTPBadRequest(text="Invalid numeric filter") from exc
    if hours not in (0, 24, 72, 168, 720) or not 1 <= limit <= (5000 if exporting else 200) or not 0 <= offset <= 100000:
        raise web.HTTPBadRequest(text="Invalid filter")
    return symbol, side, hours, limit, offset

def safe_csv(value):
    text = str(value) if value is not None else ""
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text

async def start_dashboard(host: str, port: int, snapshot_fn, history_fn, store=None):
    """No remote write operations; bind localhost or use authenticated VPN access."""
    @web.middleware
    async def secure_headers(request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc
        for key, value in SECURITY.items():
            response.headers[key] = value
        return response

    app = web.Application(middlewares=[secure_headers], client_max_size=65536)

    async def index(_):
        return web.FileResponse(ROOT / "index.html")

    async def css(_):
        return web.FileResponse(ROOT / "style.css")

    async def javascript(_):
        return web.FileResponse(ROOT / "app.js")

    async def status(_):
        return web.json_response(snapshot_fn())

    async def history(_):
        return web.json_response(history_fn())  # legacy API

    async def signals(request):
        symbol, side, hours, limit, offset = filters(request, snapshot_fn)
        if store is None:
            items = history_fn()
            return web.json_response({"items": items[:limit], "total": len(items)})
        return web.json_response({
            "items": store.history(limit=limit, symbol=symbol, side=side, hours=hours, offset=offset),
            "total": store.count(symbol=symbol, side=side, hours=hours)
        })

    async def blackbox(request):
        """Immutable explanation from alert creation; never infer old evidence."""
        if store is None:
            raise web.HTTPServiceUnavailable(text="Evidence storage unavailable")
        raw_id = request.match_info["alert_id"]
        if not raw_id.isdecimal() or len(raw_id) > 16 or int(raw_id) < 1:
            raise web.HTTPBadRequest(text="Invalid alert id")
        evidence = store.evidence(int(raw_id))
        if evidence is None:
            raise web.HTTPNotFound(text="Black Box not recorded for this alert")
        return web.json_response(evidence)

    async def performance(request):
        symbol, _side, hours, _limit, _offset = filters(request, snapshot_fn)
        if store is None:
            raise web.HTTPServiceUnavailable(text="Performance storage unavailable")
        return web.json_response(store.performance(hours=hours, symbol=symbol))

    async def accuracy_lab(request):
        if store is None:
            raise web.HTTPServiceUnavailable(text="Research storage unavailable")
        symbol, _side, hours, _limit, _offset = filters(request, snapshot_fn)
        return web.json_response(store.accuracy_lab(symbol=symbol, hours=hours))

    async def replay(request):
        if store is None:
            raise web.HTTPServiceUnavailable(text="Research storage unavailable")
        symbol = request.query.get("symbol", "").upper().strip()
        if symbol not in snapshot_fn().get("symbols", {}):
            raise web.HTTPBadRequest(text="Select a configured market")
        try:
            hours = int(request.query.get("hours", "1"))
            limit = int(request.query.get("limit", "10000"))
            data = store.replay(symbol, hours=hours, limit=limit)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="Invalid replay window") from exc
        return web.json_response(data)

    async def themes_css(_):
        return web.FileResponse(ROOT / "themes.css")

    async def research_js(_):
        return web.FileResponse(ROOT / "research.js")

    async def export_csv(request):
        symbol, side, hours, limit, offset = filters(request, snapshot_fn, exporting=True)
        if store is None:
            raise web.HTTPServiceUnavailable(text="History storage unavailable")
        rows = store.history(limit=limit, symbol=symbol, side=side, hours=hours, offset=offset)
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(["Time UTC", "Market", "Signal", "Spot price at alert",
                         "Buy pressure %", "Aggregate events", "Factors",
                         "5m move %", "15m move %", "30m move %", "60m move %",
                         "5m status", "15m status", "30m status", "60m status"])
        for item in rows:
            values = [item.get("outcomes", {}).get(str(h), {}) for h in (5, 15, 30, 60)]
            writer.writerow([safe_csv(item.get(k)) for k in (
                "time", "symbol", "side", "price", "buy_pct", "trades", "factors")] +
                [v.get("directional_move_pct") for v in values] +
                [v.get("status", "pending") for v in values])
        return web.Response(text=out.getvalue(), content_type="text/csv", charset="utf-8",
                            headers={"Content-Disposition": 'attachment; filename="zeus-signal-history.csv"'})

    async def health(_):
        s = snapshot_fn()
        healthy = bool(s.get("healthy"))
        return web.json_response({
            "ok": healthy, "stream_connected": bool(s.get("connected")),
            "ready_markets": s.get("ready_markets", 0),
            "total_markets": len(s.get("symbols", {})),
            "time_utc": datetime.now(timezone.utc).isoformat()
        }, status=200 if healthy else 503)

    app.router.add_get("/", index)
    app.router.add_get("/assets/style.css", css)
    app.router.add_get("/assets/app.js", javascript)
    app.router.add_get("/assets/themes.css", themes_css)
    app.router.add_get("/assets/research.js", research_js)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/history", history)
    app.router.add_get("/api/signals", signals)
    app.router.add_get("/api/signals/{alert_id}/blackbox", blackbox)
    app.router.add_get("/api/performance", performance)
    app.router.add_get("/api/research/accuracy", accuracy_lab)
    app.router.add_get("/api/research/replay", replay)
    app.router.add_get("/api/export.csv", export_csv)
    app.router.add_get("/api/health", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, host=host, port=port).start()
    except Exception:
        await runner.cleanup()
        raise
    return runner
