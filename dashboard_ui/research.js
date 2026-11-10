/* ZEUS V5 Research Lab: read-only market research and browser-only paper trades. */
(function () {
"use strict";
const $ = id => document.getElementById(id);
const n = (tag, cls, value) => {
  const x=document.createElement(tag);
  if(cls)x.className=cls;
  if(value!==undefined)x.textContent=String(value);
  return x;
};
const fmt=(value,digits=2)=>value==null||!Number.isFinite(Number(value))?"—":
  Number(value).toLocaleString("en-US",{maximumFractionDigits:digits});
const usd=(value,digits=2)=>value==null||!Number.isFinite(Number(value))?"—":
  "$"+Number(value).toLocaleString("en-US",{minimumFractionDigits:digits,maximumFractionDigits:digits});
const when=(epoch)=>new Date(epoch*1000).toLocaleString("en-MY",{
  timeZone:"Asia/Kuala_Lumpur",day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false
});
const NS="http://www.w3.org/2000/svg";
const createSvg=(tag,attrs={})=>{
  const element=document.createElementNS(NS,tag);
  for(const [key,value] of Object.entries(attrs))element.setAttribute(key,String(value));
  return element;
};
async function api(path){
  const r=await fetch(path,{cache:"no-store",headers:{Accept:"application/json"}});
  if(!r.ok)throw new Error("HTTP "+r.status);
  return r.json();
}
const themes=["cozy","vanilla","midnight","forest","sand"];
function initThemes(){
  let preferred="cozy";
  try{preferred=localStorage.getItem("zeus-theme-v5")||preferred;}catch(_){}
  if(!themes.includes(preferred))preferred="cozy";
  document.documentElement.dataset.theme=preferred;
  const sel=$("themeSelector");
  sel.value=preferred;
  sel.addEventListener("change",()=>{
    if(!themes.includes(sel.value))return;
    document.documentElement.dataset.theme=sel.value;
    try{localStorage.setItem("zeus-theme-v5",sel.value);}catch(_){}
  });
}
function chart(points,field,height=100){
  const valid=(points||[]).filter(p=>Number.isFinite(+p[field])&&Number.isFinite(+p.time));
  if(valid.length<2)return n("p","empty-copy","Collecting chart samples…");
  // Downsample long runs for inexpensive iPhone rendering.
  const stride=Math.max(1,Math.ceil(valid.length/800));
  const samples=valid.filter((_,i)=>i%stride===0||i===valid.length-1);
  const values=samples.map(p=>+p[field]), min=Math.min(...values),max=Math.max(...values);
  const start=samples[0].time,end=samples[samples.length-1].time;
  const span=Math.max(1,end-start), range=Math.max(max-min,Math.abs(max)*0.00001,0.0001);
  const svg=createSvg("svg",{viewBox:"0 0 800 "+height,role:"img","aria-label":field+" over recorded time"});
  const vertices=samples.map((p)=>{
    const x=12+776*(p.time-start)/span;
    const y=10+(height-20)*(1-(p[field]-min)/range);
    return x.toFixed(2)+","+y.toFixed(2);
  }).join(" ");
  const poly=createSvg("polyline",{points:vertices,fill:"none",stroke:"currentColor",
    "stroke-width":2.2,"stroke-linecap":"round","stroke-linejoin":"round"});
  svg.append(poly);svg.style.color=values[values.length-1]>=values[0]?"var(--green)":"var(--red)";
  return svg;
}
function syncSymbols(symbols){
  for(const id of ["accuracySymbol","replaySymbol"]){
    const select=$(id),old=select.value;
    const allowed=Object.keys(symbols);
    const prefix=id==="accuracySymbol"?"All markets":"Choose market";
    select.replaceChildren();
    const first=n("option","",prefix);first.value="";select.append(first);
    for(const symbol of allowed){const opt=n("option","",symbol);opt.value=symbol;select.append(opt);}
    select.value=allowed.includes(old)?old:(id==="replaySymbol"&&allowed.length?allowed[0]:"");
  }
}
let lastSymbolsKey="";
function flowCard(symbol,details){
  const c=n("article","flow-lab-card"),head=n("div","flow-lab-top");
  head.append(n("h3","",symbol),n("span","small-badge","AGG TRADE"));
  c.append(head);
  const value=n("strong","flow-lab-value",usd(details.cvd_usdt,0));
  if(Number(details.cvd_usdt)<0)value.classList.add("negative");else value.classList.add("positive");
  c.append(n("small","flow-lab-caption","Cumulative aggressive buy minus sell quote volume (this connected session)"),value);
  const canvas=n("div","flow-lab-chart");
  canvas.append(chart(details.cvd_series,"cvd_usdt"));c.append(canvas);
  const divergence={
    PRICE_UP_CVD_DOWN:"Price rising while CVD falls (potential bearish divergence)",
    PRICE_DOWN_CVD_UP:"Price falling while CVD rises (potential bullish divergence)",
    NONE:"No 60-second price/CVD divergence flagged"
  };
  c.append(n("p","flow-lab-caption",divergence[details.cvd_divergence]||"Waiting for 60s observations"));
  const tf=n("p","flow-lab-caption",
    "EMA trend: 1m "+(details.trend||"UNKNOWN")+" · 5m "+(details.trend_5m||"UNKNOWN")+" · 15m "+(details.trend_15m||"UNKNOWN"));
  c.append(tf);
  c.append(n("h3","", "Large aggregate trade events"));
  const radar=n("div","flow-radar-list"),events=details.large_trade_events||[];
  if(!events.length)radar.append(n("p","flow-lab-caption","None recorded above the threshold this session."));
  for(const trade of events.slice(0,6)){
    const item=n("div","flow-radar-row");
    item.append(n("span",trade.side==="BUY"?"positive":"negative",trade.side+" · "+usd(trade.quote_usdt,0)),
                n("small","",when(trade.time)));
    radar.append(item);
  }
  c.append(radar);
  c.append(n("p","panel-footnote","Minimum event threshold: "+usd(details.large_trade_threshold_usdt,0)+
    ". Also requires event size ≥12× recent median when available."));
  return c;
}
async function updateFlow(){
  try{
    const data=await api("/api/status");
    const symbols=data.symbols||{};
    const key=Object.keys(symbols).join(",");
    if(key!==lastSymbolsKey){syncSymbols(symbols);lastSymbolsKey=key;}
    const container=$("flowLabMarkets");
    const cards=Object.entries(symbols).map(([symbol,d])=>flowCard(symbol,d));
    container.replaceChildren(...(cards.length?cards:[n("p","empty-copy","No markets configured.")]));
  }catch(err){
    $("flowLabMarkets").replaceChildren(n("p","empty-copy","Market stream unavailable: "+err.message));
  }
}
async function updateAccuracy(){
  const q=new URLSearchParams({hours:$("accuracyPeriod").value,symbol:$("accuracySymbol").value});
  try{
    const data=await api("/api/research/accuracy?"+q);
    const tbody=$("accuracyBody");
    const results=(data.groups||[]).sort((a,b)=>{
      const order={ALL:0,BUY:1,SELL:2};
      return (order[a.group]??3)-(order[b.group]??3) ||
        a.group.localeCompare(b.group) || a.horizon_minutes-b.horizon_minutes;
    });
    if(!results.length){
      const tr=n("tr"),cell=n("td","table-empty","No observed outcomes yet. Leave ZEUS running to collect live follow-through.");
      cell.colSpan=8;tr.append(cell);tbody.replaceChildren(tr);return;
    }
    const rows=results.map(row=>{
      const tr=n("tr"),interval=row.wilson_95_pct;
      const cols=[row.group,row.horizon_minutes+"m",fmt(row.observed,0),
        row.positive_pct==null?"—":fmt(row.positive_pct,1)+"%",
        row.average_move_pct==null?"—":fmt(row.average_move_pct,4)+"%",
        row.median_move_pct==null?"—":fmt(row.median_move_pct,4)+"%",
        interval?interval[0]+"–"+interval[1]+"%":"—",
        fmt(row.missed,0)];
      for(let i=0;i<cols.length;i++)tr.append(n("td",i===3&&row.sample_warning?"return-missed":"",cols[i]));
      if(row.sample_warning)tr.title="Fewer than 30 observed outcomes. Interpret cautiously.";
      return tr;
    });
    tbody.replaceChildren(...rows);
  }catch(err){
    const row=n("tr"),cell=n("td","table-empty","Accuracy Lab unavailable: "+err.message);
    cell.colSpan=8;row.append(cell);$("accuracyBody").replaceChildren(row);
  }
}
const paper={points:[],idx:0,cash:10000,quantity:0,entryNotional:0,
  trades:[],timer:null,symbol:"",playing:false};
const FEE=0.001,SLIP=0.0005;
function stopReplay(){
  if(paper.timer!==null)clearInterval(paper.timer);
  paper.timer=null;paper.playing=false;
  $("replayPlay").textContent="▶ Play";
}
function currentPoint(){return paper.points[paper.idx]||null;}
function resetPaper(){
  paper.cash=10000;paper.quantity=0;paper.entryNotional=0;paper.trades=[];
  renderPaper();
}
function renderPaper(){
  const tick=currentPoint(),price=tick?tick.price:null;
  $("replayPrice").textContent=price!=null?usd(price,price>100?2:6):"—";
  $("replayTime").textContent=tick?when(tick.time):"—";
  const equity=paper.cash+paper.quantity*(price||0);
  $("replayEquity").textContent=usd(equity);
  $("replayPosition").textContent=paper.quantity?fmt(paper.quantity,6)+" "+paper.symbol:"Flat";
  $("replayBuy").disabled=!tick||paper.cash<10;
  $("replaySell").disabled=!tick||paper.quantity<=0;
  $("replayStep").disabled=!tick||paper.idx>=paper.points.length-1;
  const visible=tick?paper.points.slice(0,paper.idx+1):[];
  const chartArea=$("replayChart");
  chartArea.replaceChildren(visible.length>=2?chart(visible,"price",230):
    n("p","empty-copy",tick?"Step forward to reveal more of the recorded price history.":"Load recorded ticks to begin."));
  const log=$("replayTrades");
  log.replaceChildren(...(paper.trades.length?paper.trades.map(t=>n("div","flow-radar-row",t)):
    [n("span","","No virtual trades yet.")]));
}
function nextTick(){
  if(paper.idx< paper.points.length-1)paper.idx++;
  else stopReplay();
  renderPaper();
}
async function loadReplay(){
  stopReplay();const symbol=$("replaySymbol").value;
  if(!symbol)return;
  $("replayLoad").disabled=true;
  try{
    const query=new URLSearchParams({symbol,hours:$("replayHours").value,limit:"10000"});
    const data=await api("/api/research/replay?"+query);
    paper.points=(data.points||[]).filter(x=>Number.isFinite(+x.price)&&+x.price>0&&Number.isFinite(+x.time));
    paper.idx=0;paper.symbol=symbol;resetPaper();
    $("replayNote").textContent=paper.points.length+" actual price samples loaded. Start at the oldest recorded tick, advance forward without revealing future prices. Virtual funds only. Fee 0.10% + slippage 0.05% per side; no order book, no execution.";
    if(paper.points.length<2)$("replayNote").textContent="Not enough recorded ticks yet. ZEUS must run V5 to start collecting replay data.";
    renderPaper();
  }catch(err){
    paper.points=[];paper.idx=0;resetPaper();
    $("replayNote").textContent="Could not load replay: "+err.message;
    renderPaper();
  }finally{$("replayLoad").disabled=false;}
}
function paperBuy(){
  const tick=currentPoint();
  if(!tick||paper.cash<10)return;
  const notional=Math.min(paper.cash*0.2,2000);
  const execution=tick.price*(1+SLIP);
  const fee=notional*FEE;
  const qty=(notional-fee)/execution;
  paper.cash-=notional;paper.quantity+=qty;paper.entryNotional+=notional;
  paper.trades.unshift("BUY "+fmt(qty,7)+" @ "+usd(execution,4)+" incl. "+usd(fee)+" fee · "+when(tick.time));
  renderPaper();
}
function paperSell(){
  const tick=currentPoint();
  if(!tick||!paper.quantity)return;
  const execution=tick.price*(1-SLIP);
  const gross=paper.quantity*execution,fee=gross*FEE,net=gross-fee;
  const roi=100*(net-paper.entryNotional)/paper.entryNotional;
  paper.cash+=net;
  paper.trades.unshift("SELL "+fmt(paper.quantity,7)+" @ "+usd(execution,4)+" · "+usd(net)+" net · "+fmt(roi,2)+"% simulated · "+when(tick.time));
  paper.quantity=0;paper.entryNotional=0;renderPaper();
}
function initReplay(){
  $("replayLoad").addEventListener("click",loadReplay);
  $("replayStep").addEventListener("click",nextTick);
  $("replayPlay").addEventListener("click",()=>{
    if(!paper.points.length)return;
    if(paper.playing){stopReplay();return;}
    paper.playing=true;$("replayPlay").textContent="❚❚ Pause";
    paper.timer=setInterval(nextTick,300);
  });
  $("replayBuy").addEventListener("click",paperBuy);
  $("replaySell").addEventListener("click",paperSell);
  $("replayReset").addEventListener("click",()=>{
    stopReplay();paper.idx=0;resetPaper();renderPaper();
  });
  renderPaper();
}
function init(){
  initThemes();initReplay();
  $("accuracyPeriod").addEventListener("change",updateAccuracy);
  $("accuracySymbol").addEventListener("change",updateAccuracy);
  $("accuracyRefresh").addEventListener("click",updateAccuracy);
  updateFlow().then(updateAccuracy);
  setInterval(updateFlow,10000);
  setInterval(updateAccuracy,30000);
}
document.addEventListener("DOMContentLoaded",init);
})();