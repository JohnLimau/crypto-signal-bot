/* ZEUS Command Center — no frameworks, external scripts or demo trading values. */
"use strict";
const state = { status:null, performance:null, rows:[], total:0, selected:null, page:0, pageSize:40, loading:false, fetchingRows:false, lastSynced:null };
const $ = id => document.getElementById(id);
const ns = "http://www.w3.org/2000/svg";
const fmt = (value, max=2) => value == null || !Number.isFinite(Number(value)) ? "—" :
  Number(value).toLocaleString("en-US",{maximumFractionDigits:max});
const money = (value, decimals=null) => value==null || !Number.isFinite(Number(value)) ? "—" :
  "$" + Number(value).toLocaleString("en-US", {minimumFractionDigits:decimals==null?0:decimals,
    maximumFractionDigits:decimals==null?(Number(value)>=100?2:6):decimals});
const pct = (value, digits=2, sign=false) => value==null || !Number.isFinite(Number(value)) ? "—" :
  (sign && Number(value)>0?"+":"") + fmt(value,digits) + "%";
const formatDate = iso => {if(!iso)return "—"; const d=new Date(iso);
  return Number.isNaN(d.getTime())?"—":d.toLocaleString("en-MY", {timeZone:"Asia/Kuala_Lumpur",
    day:"2-digit",month:"short",year:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});};
const nowMYT = () => new Date().toLocaleTimeString("en-MY", {timeZone:"Asia/Kuala_Lumpur",hour12:false});
function node(tag, cls="", content=null) {
  const el=document.createElement(tag);
  if(cls)el.className=cls;
  if(content!==null)el.textContent=String(content);
  return el;
}
function svgNode(tag, attrs={}) {
  const el=document.createElementNS(ns,tag);
  for(const [key,value] of Object.entries(attrs))el.setAttribute(key,String(value));
  return el;
}
function swapText(id,value){$(id).textContent=String(value);}
function dot(kind="muted") {return node("span","status-dot "+(kind==="good"?"":kind==="bad"?"bad-dot":kind==="warn"?"warn-dot":"muted-dot"));}
function toast(message) {
  const n=$("toast");n.textContent=message;n.hidden=false;
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>n.hidden=true,4000);
}
function formatUptime(s) {
  if(!Number.isFinite(s))return "—";
  const days=Math.floor(s/86400),hours=Math.floor(s%86400/3600),mins=Math.floor(s%3600/60);
  return days?days+"d "+hours+"h":hours?hours+"h "+mins+"m":mins+"m";
}
function statusLabel(s) {
  if(!s)return ["Offline","Dashboard cannot reach the bot","bad"];
  if(!s.connected)return ["Stream offline","Waiting to reconnect","bad"];
  if(!s.healthy)return ["Data warming up",s.ready_markets+" of "+Object.keys(s.symbols||{}).length+" markets ready","warn"];
  return ["All systems operational",s.ready_markets+" markets ready","good"];
}
function updateStatusChrome(s) {
  const [label, detail, tone] = statusLabel(s);
  const side=$("sidebarStatusDot");side.className=dot(tone).className;
  swapText("sidebarStatus",label);swapText("sidebarDetails",detail);
  const hero=$("heroHealth");
  hero.replaceChildren(dot(tone));
  const description=node("span");description.append(node("strong","",label),node("small","",detail));hero.append(description);
  const live=$("livePill");live.replaceChildren(dot(tone),document.createTextNode(s&&s.connected?" Live spot stream":" Stream offline"));
  swapText("kpiMarkets",s ? (s.ready_markets+" / "+Object.keys(s.symbols||{}).length) : "—");
  swapText("kpiMarketsHint",s&&s.healthy?"Feeds & indicators ready":s&&s.connected?"Waiting for fresh market inputs":"Stream unavailable");
  swapText("kpiUptime",s ? formatUptime(s.system?.uptime_seconds):"—");
  swapText("kpiUptimeHint",s?"Since current bot startup":"Current process only");
  swapText("systemMode",s?.system?.alert_mode==="CONSOLE"?"CONSOLE TEST MODE":"READ-ONLY / DISCORD");
  $("lastSync").textContent=state.lastSynced?
    "Synced "+state.lastSynced.toLocaleTimeString("en-MY",{timeZone:"Asia/Kuala_Lumpur",hour12:false}):"Not synced";
}
function svgPlot(prices, width, height, large=false) {
  const svg=svgNode("svg",{viewBox:"0 0 "+width+" "+height, role:"img","aria-label":"Sampled spot price trend"});
  const clean=prices.filter(x=>x && Number.isFinite(Number(x.price)) && Number(x.price)>0);
  if(clean.length<2)return null;
  let min=Math.min(...clean.map(x=>Number(x.price))),max=Math.max(...clean.map(x=>Number(x.price)));
  const padding=large?15:3;
  const range=Math.max(max-min,Math.max(1e-10,Math.abs(min)*.00002));
  min-=range*.13;max+=range*.13;
  const ys=clean.map(p=>height-padding-(Number(p.price)-min)/(max-min)*(height-2*padding));
  const xs=clean.map((_,i)=>padding+(width-2*padding)*i/(clean.length-1));
  const coords=xs.map((x,i)=>x.toFixed(2)+","+ys[i].toFixed(2)).join(" ");
  const rising=Number(clean.at(-1).price)>=Number(clean[0].price);
  const stroke=rising?"#578367":"#bc6a5e";
  if(large) {
    const defs=svgNode("defs"),grad=svgNode("linearGradient",{id:"marketAreaFade",x1:"0",y1:"0",x2:"0",y2:"1"});
    grad.append(svgNode("stop",{offset:"0%","stop-color":stroke,"stop-opacity":"0.19"}),
                svgNode("stop",{offset:"100%","stop-color":stroke,"stop-opacity":"0.01"}));
    defs.append(grad);svg.append(defs);
    const polygon=svgNode("polygon",{points:padding+","+(height-padding)+" "+coords+" "+(width-padding)+","+(height-padding),
      fill:"url(#marketAreaFade)"});
    svg.append(polygon);
  }
  svg.append(svgNode("polyline",{points:coords,fill:"none",stroke,"stroke-width":large?2.6:2,"stroke-linecap":"round","stroke-linejoin":"round"}));
  if(large) {
    svg.append(svgNode("circle",{cx:xs.at(-1),cy:ys.at(-1),r:4,fill:stroke,stroke:"#fffefa","stroke-width":2}));
  }
  return svg;
}
function marketCard(symbol,p) {
  const button=node("button","market-card"+(state.selected===symbol?" selected":""));
  button.type="button";button.dataset.symbol=symbol;
  const top=node("div","market-top");
  const name=node("div","market-name",symbol);name.append(node("small","",symbol.endsWith("USDT")?"SPOT / USDT":"SPOT MARKET"));
  top.append(name,node("span","market-state"+(!p.trade_fresh?" offline":""),p.ready?"READY":p.trade_fresh?"WARMING UP":"NO LIVE DATA"));
  const middle=node("div","market-main"),part=node("div");
  part.append(node("div","market-price",money(p.price)),node("div","market-move "+(p.price_change_pct>=0?"positive":"negative"),
    p.price_change_pct == null?"Awaiting 60s window":pct(p.price_change_pct,3,true)+" / 60s"));
  middle.append(part);
  const mini=node("div","mini-chart");const svg=svgPlot(p.price_series||[],138,62);if(svg)mini.append(svg);middle.append(mini);
  const pressure=node("div"),label=node("div","pressure-head");
  label.append(node("span","", "Taker buy pressure"),node("strong","",p.buy_pct==null?"Warming up":pct(p.buy_pct,1)));
  const bar=node("div","pressure-track"),fill=node("span","pressure-fill");
  fill.style.width=(p.buy_pct==null?0:Math.max(0,Math.min(100,p.buy_pct)))+"%";
  bar.append(fill);pressure.append(label,bar);
  const meta=node("div","market-meta");
  const volume=node("span");volume.append("60s events ",node("strong","",fmt(p.trades,0)));
  const trend=node("span");trend.append("EMA trend ",node("strong","",p.trend||"—"));
  meta.append(volume,trend);
  button.append(top,middle,node("div","market-divider"),pressure,meta);
  return button;
}
function renderMarkets(){
  const area=$("marketCards");
  const symbols=state.status?.symbols||{};
  const keys=Object.keys(symbols);
  if(!keys.length){area.replaceChildren(node("div","empty-block","No active market feed. Check bot connection and SYMBOLS in .env."));return;}
  if(!keys.includes(state.selected))state.selected=keys[0];
  area.replaceChildren(...keys.map(sym=>marketCard(sym,symbols[sym])));
  const selector=$("symbolFilter"),previous=selector.value;
  selector.replaceChildren(node("option","", "All markets"));
  selector.firstChild.value="";
  for(const symbol of keys){const opt=node("option","",symbol);opt.value=symbol;selector.append(opt);}
  selector.value=keys.includes(previous)?previous:"";
  renderSelectedMarket();
}
function reading(label,value,cls=""){const row=node("div","reading-row");row.append(node("span","",label),node("strong",cls,value));return row;}
function renderSelectedMarket(){
  const sym=state.selected,p=state.status?.symbols?.[sym];if(!p)return;
  swapText("chartTitle",sym+" / USDT spot monitor");
  swapText("readingSymbol",sym);
  swapText("chartPrice",money(p.price));
  const change=$("chartChange");change.textContent=p.price_change_pct==null?"Awaiting 60s data":pct(p.price_change_pct,3,true)+" over 60s";
  change.className=p.price_change_pct==null?"neutral":p.price_change_pct>=0?"positive":"negative";
  const plot=$("priceChart");
  const svg=svgPlot(p.price_series||[],800,230,true);
  plot.replaceChildren(svg||node("p","empty-chart","Collecting streamed spot price samples. This chart begins filling when the bot runs."));
  $("technicalReadings").replaceChildren(
    reading("Signal inputs ready",p.ready?"YES":"NO",p.ready?"positive":"negative"),
    reading("Taker buy / sell",p.buy_pct==null?"Warming up":pct(p.buy_pct,1)+" / "+pct(p.sell_pct,1)),
    reading("Buy / sell quote volume",p.buy_quote==null?"—":money(p.buy_quote)+" / "+money(p.sell_quote)),
    reading("EMA 9 / EMA 21",p.ema_fast==null?"—":fmt(p.ema_fast,4)+" / "+fmt(p.ema_slow,4)),
    reading("RSI 14",fmt(p.rsi,1)),
    reading("Candle volume vs 20m",p.volume_ratio==null?"—":fmt(p.volume_ratio,2)+"×"),
    reading("Top-20 bid / ask liquidity",p.book_ratio==null?"—":fmt(p.book_ratio,2)+"×"),
    reading("Best bid / ask spread",p.spread_bps==null?"—":fmt(p.spread_bps,2)+" bps"),
    reading("Time since last trade",p.last_trade_age_seconds==null?"—":fmt(p.last_trade_age_seconds,1)+"s"),
    reading("Time since market REST update",p.market_data_age_seconds==null?"—":fmt(p.market_data_age_seconds,1)+"s")
  );
}
function renderPerformance() {
  const perf=state.performance;
  if(!perf)return;
  const first=perf.horizons?.["5"]||{};
  swapText("kpiSignals",fmt(perf.alert_count,0));
  swapText("kpiSignalsHint",fmt(perf.buy_count,0)+" BUY / "+fmt(perf.sell_count,0)+" SELL in selected period");
  swapText("kpiRate",first.positive_rate_pct==null?"—":pct(first.positive_rate_pct,1));
  swapText("kpiRateHint",first.observed ? first.positive+" positive out of "+first.observed+" observed" : "No measured 5m outcomes");
  swapText("perfSample",perf.alert_count+" saved alerts in selected period");
  swapText("perfCaption",(perf.symbol==="ALL"?"All markets":perf.symbol)+" / directional spot follow-through");
  const container=$("performanceRows");
  const rows=[];
  for(const h of [5,15,30,60]){
    const p=perf.horizons?.[String(h)]||{};
    const row=node("div","performance-row");
    row.append(node("span","horizon-tag",h+" MIN"));
    const track=node("div","score-track"),fill=node("span","score-fill");
    fill.style.width=(p.positive_rate_pct==null?0:p.positive_rate_pct)+"%";
    track.append(fill);row.append(track);
    row.append(node("strong","score-value",p.positive_rate_pct==null?"—":pct(p.positive_rate_pct,1)));
    row.append(node("span","sample-detail",p.observed+" measured · "+p.pending+" pending · "+p.missed+" missed · avg "+(p.average_directional_move_pct==null?"—":pct(p.average_directional_move_pct,3,true))));
    rows.push(row);
  }
  container.replaceChildren(...rows);
}
function td(text="",cls=""){return node("td",cls,text);}
function outcomeCell(outcome){
  if(!outcome||!outcome.status)return td("Pending","return-missed");
  if(outcome.status==="missed")return td("Missed","return-missed");
  const v=outcome.directional_move_pct;
  return td(pct(v,3,true),v>=0?"positive":"negative");
}
function renderHistory(){
  const body=$("historyBody");
  if(!state.rows.length){body.replaceChildren(node("tr"));const cell=td("No recorded signals match these filters yet.","table-empty");cell.colSpan=10;body.firstChild.append(cell);}
  else {
    const items=state.rows.map(r=>{
      const tr=node("tr");
      tr.append(td(formatDate(r.time)),td(r.symbol));
      const dir=td();dir.append(node("span","direction-pill "+(r.side==="BUY"?"buy":"sell"),r.side));tr.append(dir);
      tr.append(td(money(r.price)),td(pct(r.buy_pct,1)));
      tr.append(outcomeCell(r.outcomes?.["5"]),outcomeCell(r.outcomes?.["15"]),outcomeCell(r.outcomes?.["60"]));
      const factor=td();factor.append(node("span","factors",r.factors||"—"));tr.append(factor);
      const inspection=td();
      if(r.has_evidence){
        const btn=node("button","button button-quiet blackbox-open","Inspect");
        btn.type="button";btn.dataset.blackboxId=String(r.id);
        btn.setAttribute("aria-label","Inspect Black Box for "+r.symbol+" "+r.side+" signal");
        inspection.append(btn);
      }else inspection.append(node("span","return-missed","Not recorded"));
      tr.append(inspection);
      return tr;
    });
    body.replaceChildren(...items);
  }
  swapText("historyCount",fmt(state.total,0)+" signals");
  swapText("pageCaption","Showing "+state.rows.length+" of "+state.total+" saved signals");
  $("loadMoreBtn").disabled=state.fetchingRows || state.rows.length>=state.total;
}
// Evidence is rendered with textContent only: exchange data never becomes HTML.
function evidenceRow(label,value){
  const row=node("div","blackbox-metric");
  row.append(node("span","",label),node("strong","",value));
  return row;
}
function evidenceSection(label,rows){
  const section=node("section","blackbox-section");
  section.append(node("h3","",label));
  const list=node("div","blackbox-metrics");
  list.append(...rows);
  section.append(list);
  return section;
}
function evidenceChecks(label,checks){
  const section=node("section","blackbox-section");
  section.append(node("h3","",label));
  const items=node("div","blackbox-checks");
  for(const c of checks||[]){
    const row=node("div","blackbox-check");
    row.append(node("span",c.passed?"positive":"negative",c.passed?"✓ PASS":"✕ FAIL"));
    const info=node("div");
    info.append(node("strong","",c.name||"Unknown rule"));
    if(c.observed!=null)info.append(node("small","", "Observed: "+c.observed));
    if(c.required!=null)info.append(node("small","", "Required: "+c.required));
    row.append(info);items.append(row);
  }
  section.append(items);
  return section;
}
function renderBlackBox(data){
  const f=data.order_flow||{},t=data.technicals||{},g=data.gate||{};
  $("blackboxTitle").textContent=(data.symbol||"Unknown market")+" / "+(data.side||"") +" decision";
  $("blackboxMeta").textContent=formatDate(data.captured_at_utc)+" · Recorded when the signal was evaluated";
  $("blackboxContent").replaceChildren(
    evidenceSection("Market snapshot",[
      evidenceRow("Spot reference price",money(data.reference_price)),
      evidenceRow("Aggressive buy / sell pressure",pct(f.buy_pressure_pct,2)+" / "+pct(f.sell_pressure_pct,2)),
      evidenceRow("Aggressive buy / sell quote volume",money(f.buy_quote_usdt)+" / "+money(f.sell_quote_usdt)),
      evidenceRow("Aggregate trade events",fmt(f.aggregate_events,0)),
      evidenceRow("Window observed",fmt(f.covered_seconds,1)+"s of "+fmt(f.window_seconds,0)+"s"),
      evidenceRow("Price change in window",pct(f.price_change_pct,4,true)),
      evidenceRow("EMA 9 / EMA 21",fmt(t.ema_9,5)+" / "+fmt(t.ema_21,5)),
      evidenceRow("5m / 15m EMA context",(t.trend_5m||"Not captured")+" / "+(t.trend_15m||"Not captured")),
      evidenceRow("RSI 14",fmt(t.rsi_14,2)),
      evidenceRow("20-level bid / ask liquidity",fmt(t.bid_ask_liquidity_ratio,3)+"×"),
      evidenceRow("Spread",fmt(t.spread_bps,3)+" bps"),
      evidenceRow("Candle volume ratio",fmt(t.volume_ratio,3)+"×")
    ]),
    evidenceChecks("Mandatory filters",data.rules),
    evidenceChecks("Directional confirmation checks",data.directional_checks),
    evidenceSection("Signal gate",[
      evidenceRow("Directional checks",fmt(data.passed_directional_checks,0)+" / 4 (minimum "+fmt(data.required_directional_checks,0)+")"),
      evidenceRow("Required consecutive evaluations",fmt(g.required_consecutive_evaluations,0)),
      evidenceRow("Strict 5m / 15m gate",g.mtf_gate_enabled?"ENABLED":"OFF (context only)"),
      evidenceRow("Cooldown setting",fmt(g.cooldown_seconds,0)+"s"),
      evidenceRow("Neutral evaluations to rearm",fmt(g.neutral_evaluations_to_rearm,0))
    ]),
    node("p","blackbox-disclaimer",data.disclaimer||"Research evidence, not a profitability claim.")
  );
}
async function openBlackBox(id){
  const dialog=$("blackboxDialog");
  $("blackboxTitle").textContent="Decision evidence";
  $("blackboxMeta").textContent="Loading signal #"+id;
  $("blackboxContent").replaceChildren(node("p","empty-copy","Loading saved decision snapshot…"));
  if(!dialog.open)dialog.showModal();
  try{
    const evidence=await fetchJSON("/api/signals/"+encodeURIComponent(id)+"/blackbox");
    if(!dialog.open)return;
    renderBlackBox(evidence);
  }catch(e){
    if(dialog.open)$("blackboxContent").replaceChildren(node("p","empty-copy","Evidence unavailable: "+e.message));
  }
}
function service(label,value,tone){
  const row=node("div","service-row"),left=node("span","service-name");
  left.append(dot(tone),document.createTextNode(label));
  row.append(left,node("span","service-value "+(tone==="bad"?"negative":tone==="good"?"positive":"neutral"),value));return row;
}
function setting(label,value){return reading(label,value);}
function renderSystem(){
  const s=state.status;if(!s)return;
  const sys=s.system||{},sym=s.symbols||{};
  const services=[
    service("Exchange WebSocket",s.connected?"CONNECTED":"DISCONNECTED",s.connected?"good":"bad"),
    service("Ready signal markets",(s.ready_markets||0)+" / "+Object.keys(sym).length,s.healthy?"good":"warn"),
    service("Discord/console delivered (this run)",fmt(sys.success_count_since_start,0),"good"),
    service("Discord failures (this run)",fmt(sys.failure_count_since_start,0),sys.failure_count_since_start?"bad":"muted"),
    service("Last delivery",formatDate(sys.last_delivery_success_utc),sys.last_delivery_success_utc?"good":"muted"),
    service("Recent delivery failure",formatDate(sys.last_delivery_failure_utc),sys.last_delivery_failure_utc?"bad":"muted")
  ];
  for(const [symbol,p] of Object.entries(sym)){
    services.push(service(symbol+" trade feed",p.last_trade_age_seconds==null?"Awaiting trades":fmt(p.last_trade_age_seconds,1)+"s ago",p.trade_fresh?"good":"bad"));
    services.push(service(symbol+" technical data",p.market_data_age_seconds==null?"Not fetched":fmt(p.market_data_age_seconds,1)+"s ago",p.indicators_fresh?"good":"bad"));
  }
  $("serviceHealth").replaceChildren(...services);
  const error=$("lastError");error.hidden=!s.last_error;error.textContent=s.last_error?"Last error: "+s.last_error:"";
  const c=sys.settings||{};
  $("engineSettings").replaceChildren(
    setting("Pressure threshold",c.pressure_threshold_pct==null?"—":pct(c.pressure_threshold_pct,1)),
    setting("Analysis window",c.window_seconds==null?"—":c.window_seconds+" seconds"),
    setting("Minimum aggregate events",fmt(c.min_events,0)),
    setting("Additional checks",c.confirmation_checks==null?"—":c.confirmation_checks+" of 4"),
    setting("Consecutive evaluations",fmt(c.consecutive_evaluations,0)),
    setting("Signal cooldown",c.cooldown_seconds==null?"—":c.cooldown_seconds+" seconds"),
    setting("Evaluation interval",c.evaluation_seconds==null?"—":c.evaluation_seconds+" seconds"),
    setting("Minimum candle volume ratio",c.minimum_volume_ratio==null?"—":fmt(c.minimum_volume_ratio,2)+"×"),
    setting("Dashboard access",sys.dashboard_local_only?"LOCALHOST ONLY":"CUSTOM HOST — SECURE IT")
  );
}
async function fetchJSON(path) {
  const response=await fetch(path,{cache:"no-store",headers:{"Accept":"application/json"}});
  if(response.status===401){window.location.replace("/login");throw new Error("Session expired");}
  if(!response.ok)throw new Error("HTTP "+response.status+" loading "+path);
  return response.json();
}
function filterParams(limit=state.pageSize, offset=0){
  const q=new URLSearchParams();
  if($("symbolFilter").value)q.set("symbol",$("symbolFilter").value);
  if($("sideFilter").value)q.set("side",$("sideFilter").value);
  q.set("hours",$("periodFilter").value);
  q.set("limit",String(limit));q.set("offset",String(offset));
  return q;
}
async function fetchHistory(more=false) {
  if(state.fetchingRows)return;
  state.fetchingRows=true;$("loadMoreBtn").disabled=true;
  try{
    const offset=more?state.rows.length:0;
    const data=await fetchJSON("/api/signals?"+filterParams(state.pageSize,offset).toString());
    if(!more)state.rows=[];
    state.rows.push(...(data.items||[]));
    state.total=Number(data.total)||0;
    renderHistory();
  }catch(e){toast("Could not load saved signals: "+e.message);}
  finally{state.fetchingRows=false;renderHistory();}
}
async function fetchPerformance(){
  try{
    const q=filterParams();q.delete("side");q.delete("limit");q.delete("offset");
    const data=await fetchJSON("/api/performance?"+q.toString());
    state.performance=data;renderPerformance();
  }catch(e){toast("Could not load performance data: "+e.message);}
}
async function poll() {
  if(state.loading)return;
  state.loading=true;
  try {
    state.status=await fetchJSON("/api/status");
    state.lastSynced=new Date();updateStatusChrome(state.status);renderMarkets();renderSystem();
  } catch(e) {
    state.status=null;
    updateStatusChrome(null);renderMarkets();
    toast("Dashboard disconnected. Retrying automatically.");
  } finally{state.loading=false;}
}
async function refreshAll(){
  await poll();
  await Promise.all([fetchHistory(),fetchPerformance()]);
}
function bind(){
  $("logoutBtn").addEventListener("click",async()=>{
    try{await fetch("/api/auth/logout",{method:"POST",credentials:"same-origin",cache:"no-store"});}catch(_){/* Also clear the UI on failure */}
    window.location.replace("/login");
  });
  $("refreshBtn").addEventListener("click",()=>refreshAll());
  $("marketCards").addEventListener("click",event=>{
    const button=event.target.closest("[data-symbol]");
    if(!button)return;
    state.selected=button.dataset.symbol;renderMarkets();
  });
  for(const id of ["symbolFilter","sideFilter","periodFilter"]){
    $(id).addEventListener("change",()=>{fetchHistory();fetchPerformance();});
  }
  $("loadMoreBtn").addEventListener("click",()=>fetchHistory(true));
  $("historyBody").addEventListener("click",event=>{
    const button=event.target.closest("button[data-blackbox-id]");
    if(button)openBlackBox(button.dataset.blackboxId);
  });
  $("closeBlackBoxBtn").addEventListener("click",()=>$("blackboxDialog").close());
  $("exportBtn").addEventListener("click",()=>{
    const url="/api/export.csv?"+filterParams(5000).toString();
    window.location.assign(url);
    toast("Preparing CSV export from saved alerts.");
  });
  document.querySelectorAll("[data-nav]").forEach(a=>a.addEventListener("click",()=>{
    document.querySelectorAll("[data-nav]").forEach(x=>x.classList.toggle("active",x===a));
  }));
  swapText("myClock","MYT · "+nowMYT());
  setInterval(()=>swapText("myClock","MYT · "+nowMYT()),1000);
  setInterval(poll,5000);
  setInterval(()=>{fetchHistory();fetchPerformance();},20000);
}
document.addEventListener("DOMContentLoaded",()=>{bind();refreshAll();});
