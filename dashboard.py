"""Local-only lightweight dashboard; no external frontend/CDN required."""
from aiohttp import web

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zeus Signal Bot V2</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{background:#0b1220;color:#e5edf9;font:15px system-ui,Arial;margin:0}main{max-width:1100px;margin:40px auto;padding:0 20px}
h1{font-size:28px;margin-bottom:4px}.sub{color:#94a3b8;margin-top:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin:24px 0}.card{background:#151f30;border:1px solid #2a3a51;border-radius:14px;padding:20px}.pair{font-size:20px;font-weight:700}.price{font-size:28px;margin:10px 0}.small{font-size:13px;color:#9fb0c8}.buy{color:#4ade80}.sell{color:#fb7185}.muted{color:#94a3b8}table{border-collapse:collapse;width:100%}td,th{padding:11px 8px;text-align:left;border-bottom:1px solid #304158}th{color:#9fb0c8;font-size:13px}footer{color:#94a3b8;font-size:12px;margin:20px 0}#health{font-weight:600}</style></head>
<body><main><h1>ZEUS / SIGNAL BOT V2</h1><p class="sub">Live Binance spot research alerts. <span id="health">Connecting...</span> · Time: Malaysia (MYT)</p>
<div id="pairs" class="grid"></div><div class="card"><h2>Alert history</h2><div style="overflow-x:auto"><table><thead><tr><th>Time (MYT)</th><th>Market</th><th>Signal</th><th>Price</th><th>Buy pressure</th><th>Confirmations</th></tr></thead><tbody id="history"></tbody></table></div></div>
<footer>Aggregate events are not individual trades. This is not a trading system or a prediction of profitability. Dashboard binds to localhost by default.</footer></main>
<script>
const el=id=>document.getElementById(id);const fmt=n=>n==null?'—':Number(n).toLocaleString(undefined,{maximumFractionDigits:4});
async function refresh(){try{const [s,h]=await Promise.all([fetch('/api/status').then(r=>r.json()),fetch('/api/history').then(r=>r.json())]);
el('health').textContent=s.connected?'● Stream connected':'○ Stream offline / reconnecting';el('health').className=s.connected?'buy':'sell';
el('pairs').replaceChildren();for(const [sym,p] of Object.entries(s.symbols)){const c=document.createElement('div');c.className='card';const title=document.createElement('div');title.className='pair';title.textContent=sym;const price=document.createElement('div');price.className='price';price.textContent=p.price==null?'Waiting for data':('$'+fmt(p.price));const d=document.createElement('div');d.className='small';d.textContent='Buy pressure: '+(p.buy_pct==null?'warming up':p.buy_pct.toFixed(1)+'%')+' | Events: '+p.trades+' | 1m trend: '+(p.trend||'waiting')+' | RSI: '+(p.rsi==null?'—':p.rsi.toFixed(1));c.append(title,price,d);el('pairs').append(c)}
el('history').replaceChildren();for(const row of h){const tr=document.createElement('tr');const ts=new Date(row.time).toLocaleString('en-MY',{timeZone:'Asia/Kuala_Lumpur'});for(const value of [ts,row.symbol,row.side,fmt(row.price),row.buy_pct+'%',row.factors]){const td=document.createElement('td');td.textContent=value;if(value===row.side)td.className=row.side==='BUY'?'buy':'sell';tr.append(td)}el('history').append(tr)}
}catch(e){el('health').textContent='Dashboard connection interrupted';el('health').className='sell'}}refresh();setInterval(refresh,5000);
</script></body></html>'''


async def start_dashboard(host: str, port: int, snapshot_fn, history_fn):
    app = web.Application()
    async def index(_):
        return web.Response(text=PAGE, content_type="text/html", headers={"Cache-Control":"no-store"})
    async def status(_):
        return web.json_response(snapshot_fn(), headers={"Cache-Control":"no-store"})
    async def history(_):
        return web.json_response(history_fn(), headers={"Cache-Control":"no-store"})
    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/history", history)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    try:
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    return runner
