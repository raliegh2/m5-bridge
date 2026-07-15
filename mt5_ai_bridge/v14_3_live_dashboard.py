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
  const d=await r.json(), a=d.account||{}, ps=d.positions||[], ds=d.decisions||[], es=d.engines||[];
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
  document.getElementById('engines').innerHTML=es.map(e=>`<div class="engine"><div class="name">${esc(e.engine)}</div>
    <div class="sub">${esc(e.symbol)} · ${esc(e.mode)}</div><div class="state ${e.status==='SIGNAL'?'good':'wait'}">${esc(e.status)}</div>
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
