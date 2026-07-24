"""Local web dashboard for the V14.3 satellite MT5 runner.

The dashboard is intentionally read-only. It serves a single HTML page and a JSON
snapshot endpoint on localhost. The runner updates the snapshot atomically after
each market scan and opens the page automatically unless disabled.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>V14.3 Satellite Live Dashboard</title>
<style>
:root{
  color-scheme:dark;
  --bg:#07111f;--panel:#0d1b2d;--panel2:#10233a;--line:#203754;
  --text:#eef6ff;--muted:#8fa9c5;--good:#4ade80;--bad:#fb7185;
  --warn:#fbbf24;--accent:#38bdf8;--purple:#c084fc
}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#050b14,#0a1728 58%,#07111f);
font:14px/1.45 Inter,Segoe UI,Arial,sans-serif;color:var(--text);min-height:100vh}
header{position:sticky;top:0;z-index:5;background:rgba(5,11,20,.92);backdrop-filter:blur(12px);
border-bottom:1px solid var(--line);padding:14px 18px;display:flex;gap:16px;align-items:center;justify-content:space-between}
h1{font-size:18px;margin:0}.status{display:flex;gap:8px;align-items:center;color:var(--muted)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--warn);box-shadow:0 0 14px currentColor}
.dot.on{background:var(--good)}.dot.bad{background:var(--bad)}
main{max-width:1500px;margin:auto;padding:18px}
.grid{display:grid;grid-template-columns:repeat(6,minmax(140px,1fr));gap:12px}
.card,.panel{background:linear-gradient(160deg,var(--panel2),var(--panel));border:1px solid var(--line);
border-radius:14px;box-shadow:0 12px 30px rgba(0,0,0,.18)}
.card{padding:14px}.label{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.value{font-size:23px;font-weight:750;margin-top:5px}.sub{color:var(--muted);font-size:12px;margin-top:3px}
.panel{padding:16px;margin-top:14px}h2{font-size:15px;margin:0 0 12px}
.tablewrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:800px}
th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.buy,.good{color:var(--good)}.sell,.bad{color:var(--bad)}.wait,.warn{color:var(--warn)}
.badge{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:11px}
.badge.auto{border-color:#7c3aed;color:#e9d5ff}.badge.read{border-color:#0284c7;color:#bae6fd}
.reason{max-width:520px;color:#cbdced}.meta{font-family:ui-monospace,Consolas,monospace;color:var(--muted);font-size:12px}
.enginegrid{display:grid;grid-template-columns:repeat(3,minmax(240px,1fr));gap:10px}
.engine{padding:12px;border:1px solid var(--line);border-radius:12px;background:rgba(5,11,20,.28)}
.engine .name{font-weight:700}.engine .state{margin-top:6px}.engine .why{color:var(--muted);font-size:12px;margin-top:5px}
.empty{color:var(--muted);padding:14px 0}.footer{color:var(--muted);font-size:12px;margin:18px 2px}
@media(max-width:1050px){.grid{grid-template-columns:repeat(3,1fr)}.enginegrid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:650px){header{align-items:flex-start;flex-direction:column}.grid{grid-template-columns:repeat(2,1fr)}
.enginegrid{grid-template-columns:1fr}main{padding:10px}.value{font-size:19px}}
</style>
</head>
<body>
<header>
  <div><h1>V14.3 Satellite Live Dashboard</h1><div class="status"><span id="dot" class="dot"></span>
  <span id="headline">Waiting for the runner…</span></div></div>
  <div><span id="mode" class="badge read">READ_ONLY</span> <span id="updated" class="sub"></span></div>
</header>
<main>
<section class="grid">
  <div class="card"><div class="label">Balance</div><div class="value" id="balance">—</div></div>
  <div class="card"><div class="label">Equity</div><div class="value" id="equity">—</div></div>
  <div class="card"><div class="label">Floating P/L</div><div class="value" id="floating">—</div></div>
  <div class="card"><div class="label">Open trades</div><div class="value" id="open">0</div></div>
  <div class="card"><div class="label">Signals this scan</div><div class="value" id="signals">0</div></div>
  <div class="card"><div class="label">Scan latency</div><div class="value" id="latency">—</div><div class="sub">1-second target loop</div></div>
</section>

<section class="panel"><h2>Automatic runner wiring &amp; scan schedule</h2>
<p id="wiring" class="sub"></p><div class="tablewrap"><table>
<thead><tr><th>Scan group</th><th>Trigger</th><th>Candles read</th><th>Last scan</th><th>Enabled</th></tr></thead>
<tbody id="schedule"></tbody></table></div></section>

<section class="panel"><h2>Broker order flow <span class="badge read">OBSERVE ONLY</span></h2>
<p class="sub">Broker-local quote-tick direction, spread and depth-of-market when available.
Spot FX has no centralized exchange order book; these readings do not block orders.</p>
<div class="tablewrap"><table><thead><tr><th>Symbol</th><th>Pressure</th><th>Buy / Sell</th>
<th>30s / 2m / 15m</th><th>Absorption proxy</th><th>Spread shock</th>
<th>Spread</th><th>Ticks</th><th>Market depth</th><th>Updated</th></tr></thead>
<tbody id="orderflow"></tbody></table></div><div id="orderflow_empty" class="empty">Waiting for broker tick data.</div></section>

<section class="panel"><h2>Centralized CME futures order flow</h2>
<p class="sub">Databento CME Globex MBP-10 depth. Used as the preferred
candidate-time flow source when fresh; otherwise the system fails open to
broker spot-tick telemetry.</p>
<div class="tablewrap"><table><thead><tr><th>Spot symbol</th><th>Proxy</th>
<th>State</th><th>Imbalance</th><th>Depth levels</th><th>Events</th>
<th>Provider</th></tr></thead><tbody id="futuresflow"></tbody></table></div>
<div id="futuresflow_empty" class="empty">Futures provider is not configured.</div></section>

<section class="panel"><h2>Candidate order-flow decisions <span id="flowmode" class="badge read">SHADOW ONLY</span></h2>
<p class="sub">Measured immediately before execution. A conflict is recorded as a hypothetical block,
but cannot suppress the actual order until forward evidence validates the filter.</p>
<div class="tablewrap"><table><thead><tr><th>Time</th><th>Symbol</th><th>Engine</th>
<th>Side</th><th>Source</th><th>Verdict</th><th>Side confirmation</th><th>Directional imbalance</th><th>DOM direction</th>
<th>Would block</th><th>Actual result</th></tr></thead>
<tbody id="flowdecisions"></tbody></table></div>
<div id="flowdecisions_empty" class="empty">No trade candidates have reached the executor since start.</div></section>

<section class="panel"><h2>Order-flow forward validation</h2>
<p class="sub">Separate engine/timeframe buckets need at least 200 closed live
candidates and must improve net R, profit factor and drawdown in both chronological halves.</p>
<div class="tablewrap"><table><thead><tr><th>Engine</th><th>Timeframe</th>
<th>Closed</th><th>Required</th><th>Status</th><th>Eligible</th></tr></thead>
<tbody id="flowforward"></tbody></table></div>
<div id="flowforward_empty" class="empty">Collecting the first closed candidate outcomes.</div></section>

<section class="panel"><h2>Persistent scan audit &amp; missed-bar recovery</h2>
<p class="sub">Every scheduled candle scan is journaled, including zero-candidate scans,
rule rejections, detected downtime gaps, catch-up evaluations, and errors.</p>
<h3>Latest result by scan group</h3>
<div class="tablewrap"><table><thead><tr><th>Scope</th><th>Recorded</th>
<th>Outcome</th><th>Completed bar</th><th>Candidates</th><th>Details</th></tr></thead>
<tbody id="scanlatest"></tbody></table></div>
<div id="scanlatest_empty" class="empty">No completed scan groups recorded yet.</div>
<h3>Rolling event history</h3>
<div class="tablewrap"><table><thead><tr><th>Recorded</th><th>Scope</th><th>Outcome</th>
<th>Completed bar</th><th>Candidates</th><th>Details</th></tr></thead>
<tbody id="scanaudit"></tbody></table></div><div id="scanaudit_empty" class="empty">No persistent scan records yet.</div></section>

<section class="panel"><h2>Engine status</h2><div id="engines" class="enginegrid"></div></section>

<section class="panel"><h2>Open positions</h2><div class="tablewrap"><table>
<thead><tr><th>Ticket</th><th>Symbol</th><th>Engine</th><th>Side</th><th>Volume</th>
<th>Entry</th><th>Current</th><th>SL</th><th>TP</th><th>P/L</th></tr></thead>
<tbody id="positions"></tbody></table></div><div id="positions_empty" class="empty">No open positions.</div></section>

<section class="panel"><h2>Decision rationale</h2><p class="sub">This is an explicit rule trace—not hidden chain-of-thought.
It shows the engine, setup, market facts, risk controls and final result code.</p>
<div class="tablewrap"><table><thead><tr><th>Time</th><th>Symbol</th><th>Engine</th><th>Setup</th>
<th>Side</th><th>Risk</th><th>Result</th><th>Rationale / rule facts</th></tr></thead>
<tbody id="decisions"></tbody></table></div><div id="decisions_empty" class="empty">No decision records yet.</div></section>

<section class="panel"><h2>Generation diagnostics</h2><pre id="generation" class="meta">{}</pre></section>
<div class="footer">Local dashboard only. Keep the terminal and runner window open. Press Ctrl+C in PowerShell to stop the runner.</div>
</main>
<script>
const money=v=>v===null||v===undefined?'—':new Intl.NumberFormat(undefined,{style:'currency',currency:'USD'}).format(Number(v));
const num=(v,d=5)=>v===null||v===undefined?'—':Number(v).toFixed(d);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function row(cells){return '<tr>'+cells.map(x=>'<td>'+x+'</td>').join('')+'</tr>'}
function clsSide(v){v=String(v||'').toUpperCase();return v==='BUY'?'buy':v==='SELL'?'sell':'wait'}
async function refresh(){
 try{
  const r=await fetch('/data?ts='+Date.now(),{cache:'no-store'}); if(!r.ok)throw new Error(r.status);
  const d=await r.json(), a=d.account||{}, ps=d.positions||[], ds=d.decisions||[], es=d.engines||[],
    os=d.order_flow||[], ff=d.futures_order_flow||[], ofs=d.order_flow_shadow||[],
    off=d.order_flow_forward||[],
    sc=d.scan_schedule||{}, rw=d.runner_wiring||{}, sa=d.scan_audit||{};
  document.getElementById('dot').className='dot '+(d.runner_status==='RUNNING'?'on':'bad');
  document.getElementById('headline').textContent=(d.runner_status||'UNKNOWN')+' · '+(a.server||'MT5');
  const mode=String(d.execution_mode||'READ_ONLY'); const mb=document.getElementById('mode');
  mb.textContent=mode; mb.className='badge '+(mode==='AUTO'?'auto':'read');
  document.getElementById('updated').textContent=d.generated_at?'Updated '+new Date(d.generated_at).toLocaleTimeString():'';
  document.getElementById('balance').textContent=money(a.balance);
  document.getElementById('equity').textContent=money(a.equity);
  const f=Number(a.floating_profit||0), fe=document.getElementById('floating');fe.textContent=money(f);fe.className='value '+(f>=0?'good':'bad');
  document.getElementById('open').textContent=ps.length;
  document.getElementById('signals').textContent=d.candidate_count||0;
  document.getElementById('latency').textContent=(d.scan_latency_ms??'—')+' ms';
  document.getElementById('wiring').textContent=(rw.automatic_runner_connected?'CONNECTED':'NOT CONFIRMED')+
    ' / '+esc(rw.connected_engine_count??es.length)+' engines / '+esc(rw.executor||'executor unknown')+
    ' / '+esc(rw.order_path||'');
  document.getElementById('schedule').innerHTML=Object.entries(sc).map(([name,x])=>row([
    esc(name),esc(x.trigger||'-'),esc((x.timeframes||[]).join(' + ')||'-'),
    esc(x.last_scan_at?new Date(x.last_scan_at).toLocaleString():'Not scanned since start'),
    `<span class="${x.enabled===false?'warn':'good'}">${x.enabled===false?'OFF':'ON'}</span>`])).join('');
  document.getElementById('orderflow').innerHTML=os.map(x=>row([
    esc(x.symbol),`<span class="${String(x.state).startsWith('BULL')?'good':String(x.state).startsWith('BEAR')?'bad':'wait'}">${esc(x.state)}</span>`,
    esc(x.buy_pressure_percent??'-')+'% / '+esc(x.sell_pressure_percent??'-')+'%',
    ['30s','2m','15m'].map(w=>esc(x.pressure_windows?.[w]?.imbalance??'-')).join(' / '),
    esc(x.absorption?.state??'-')+(x.absorption?.score!=null?' ('+esc(x.absorption.score)+')':''),
    esc(x.spread_shock?.state??'-')+(x.spread_shock?.ratio!=null?' '+esc(x.spread_shock.ratio)+'x':''),
    esc(x.spread_pips??'-')+' pips',esc(x.tick_count??'-'),
    x.market_depth?.available?esc(x.market_depth.levels)+' levels; '+esc(x.market_depth.imbalance):'Unavailable',
    esc(x.updated_at?new Date(x.updated_at).toLocaleTimeString():'-')])).join('');
  document.getElementById('orderflow_empty').style.display=os.length?'none':'block';
  document.getElementById('futuresflow').innerHTML=ff.map(x=>row([
    esc(x.spot_symbol),
    esc((x.proxies||[]).map(p=>p.futures_symbol).join(' + ')||'-'),
    `<span class="${x.state==='READY'?'good':x.state==='ERROR'?'bad':'wait'}">${esc(x.state||x.status)}</span>`,
    esc(x.imbalance??'-'),esc(x.levels??'-'),esc(x.event_count??'-'),
    esc(x.provider)+(x.error?`<div class="bad">${esc(x.error)}</div>`:'')])).join('');
  document.getElementById('futuresflow_empty').style.display=ff.length?'none':'block';
  document.getElementById('flowmode').textContent=String(d.order_flow_shadow_mode||'SHADOW_ONLY').replaceAll('_',' ');
  document.getElementById('flowdecisions').innerHTML=ofs.map(x=>row([
    esc(x.evaluated_at?new Date(x.evaluated_at).toLocaleTimeString():'-'),esc(x.symbol),
    esc(x.engine),`<span class="${clsSide(x.side)}">${esc(x.side)}</span>`,
    esc(x.verdict_source||'BROKER_SPOT_TICKS'),
    `<span class="${x.verdict==='ALIGNED'?'good':x.verdict==='CONFLICT'?'bad':'wait'}">${esc(x.verdict)}</span>`,
    esc(x.side_confirmation||'-'),
    esc(x.directional_imbalance??'-'),esc(x.directional_depth_imbalance??'-'),
    `<span class="${x.hypothetical_block?'bad':'good'}">${x.hypothetical_block?'YES':'NO'}</span>`,
    esc(x.actual_result_code||'-')])).join('');
  document.getElementById('flowdecisions_empty').style.display=ofs.length?'none':'block';
  document.getElementById('flowforward').innerHTML=off.map(x=>row([
    esc(x.engine),esc(x.timeframe),esc(x.closed_candidates??0),
    esc(x.required_candidates??200),
    `<span class="${x.status==='PASSED'?'good':x.status==='FAILED'?'bad':'wait'}">${esc(x.status)}</span>`,
    `<span class="${x.eligible?'good':'wait'}">${x.eligible?'YES':'NO'}</span>`])).join('');
  document.getElementById('flowforward_empty').style.display=off.length?'none':'block';
  const latestScans=Object.values(sa.latest_by_scope||{}).sort((a,b)=>
    String(a.scope||'').localeCompare(String(b.scope||'')));
  document.getElementById('scanlatest').innerHTML=latestScans.map(x=>row([
    esc(x.scope),
    esc(x.recorded_at?new Date(x.recorded_at).toLocaleTimeString():'-'),
    `<span class="${String(x.outcome).includes('ERROR')?'bad':String(x.outcome).includes('COMPLETED')||String(x.outcome).includes('NO_SETUP')?'good':'wait'}">${esc(x.outcome)}</span>`,
    esc(x.completed_bar_time?new Date(Number(x.completed_bar_time)*1000).toLocaleString():'-'),
    esc(x.candidate_count??'-'),
    `<div class="meta">${esc(JSON.stringify(x.details||{}))}</div>`])).join('');
  document.getElementById('scanlatest_empty').style.display=latestScans.length?'none':'block';
  const scanEvents=sa.recent_events||[];
  document.getElementById('scanaudit').innerHTML=scanEvents.map(x=>row([
    esc(x.recorded_at?new Date(x.recorded_at).toLocaleTimeString():'-'),esc(x.scope),
    `<span class="${String(x.outcome).includes('ERROR')||String(x.outcome).includes('EXCEEDED')?'bad':String(x.outcome).includes('COMPLETED')||String(x.outcome).includes('PROCESSED')?'good':'wait'}">${esc(x.outcome)}</span>`,
    esc(x.completed_bar_time?new Date(Number(x.completed_bar_time)*1000).toLocaleString():'-'),
    esc(x.candidate_count??'-'),`<div class="meta">${esc(JSON.stringify(x.details||{}))}</div>`])).join('');
  document.getElementById('scanaudit_empty').style.display=scanEvents.length?'none':'block';
  document.getElementById('engines').innerHTML=es.map(e=>`<div class="engine"><div class="name">${esc(e.engine)}</div>
    <div class="sub">${esc(e.symbol)} / ${esc(e.mode)} / reads ${esc((e.timeframes||[]).join('/'))} / trigger ${esc(e.trigger||'-')}</div>
    <div class="sub ${e.automatic_runner_connected?'good':'bad'}">${e.automatic_runner_connected?'AUTO CONNECTED':'AUTO NOT CONFIRMED'} / last scan ${esc(e.last_scan_at?new Date(e.last_scan_at).toLocaleTimeString():'not yet')}</div>
    <div class="state ${['SIGNAL','LAST_FILLED'].includes(e.status)?'good':e.status==='LAST_REJECTED'?'bad':'wait'}">${esc(e.status)}</div>
    <div class="why">${esc(e.rationale)}</div></div>`).join('')||'<div class="empty">No engine registry.</div>';
  document.getElementById('positions').innerHTML=ps.map(p=>row([
    esc(p.ticket),esc(p.symbol),esc(p.engine||'UNMAPPED'),`<span class="${clsSide(p.side)}">${esc(p.side)}</span>`,
    esc(p.volume),num(p.price_open),num(p.price_current),num(p.sl),num(p.tp),
    `<span class="${Number(p.profit||0)>=0?'good':'bad'}">${money(p.profit)}</span>`])).join('');
  document.getElementById('positions_empty').style.display=ps.length?'none':'block';
  document.getElementById('decisions').innerHTML=ds.map(x=>row([
    esc(x.time?new Date(x.time).toLocaleTimeString():'—'),esc(x.symbol),esc(x.engine),esc(x.setup),
    `<span class="${clsSide(x.side)}">${esc(x.side)}</span>`,esc(x.risk_percent??'—')+'%',
    `<span class="${x.ok?'good':'warn'}">${esc(x.code)}</span>`,
    `<div class="reason">${esc(x.rationale)}</div><div class="meta">${esc(JSON.stringify(x.metadata||{}))}</div>`])).join('');
  document.getElementById('decisions_empty').style.display=ds.length?'none':'block';
  document.getElementById('generation').textContent=JSON.stringify(d.generation||{},null,2);
 }catch(e){
  document.getElementById('dot').className='dot bad';
  document.getElementById('headline').textContent='Dashboard cannot reach runner data';
 }
}
refresh();setInterval(refresh,1000);
</script>
</body></html>"""


class _DashboardHandler(BaseHTTPRequestHandler):
    snapshot_path: Path
    html: str = DASHBOARD_HTML

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send(200, "text/html; charset=utf-8", self.html.encode("utf-8"))
            return
        if path == "/data":
            try:
                payload = self.snapshot_path.read_bytes()
            except FileNotFoundError:
                payload = b'{"runner_status":"STARTING"}'
            self._send(200, "application/json; charset=utf-8", payload)
            return
        self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class LiveDashboard:
    """Own the localhost dashboard server and atomic JSON snapshot."""

    def __init__(
        self,
        snapshot_path: str | Path,
        host: str = "127.0.0.1",
        port: int = 8814,
    ) -> None:
        self.snapshot_path = Path(snapshot_path)
        self.host = host
        self.port = int(port)
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def write(self, payload: dict[str, Any]) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_suffix(self.snapshot_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        temporary.replace(self.snapshot_path)

    def start(self, open_browser: bool = True) -> None:
        handler = type(
            "V143DashboardHandler",
            (_DashboardHandler,),
            {"snapshot_path": self.snapshot_path},
        )
        self.server = ThreadingHTTPServer((self.host, self.port), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="v14-3-dashboard",
            daemon=True,
        )
        self.thread.start()
        if open_browser:
            threading.Timer(0.5, lambda: webbrowser.open(self.url)).start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
