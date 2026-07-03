"""Combined one-second V12 scanner and live dashboard.

Run with:
    python v12_final_dashboard.py

The process connects to MT5, scans the final five-symbol strategy continuously,
serves a responsive dashboard on http://127.0.0.1:8800, and refreshes dashboard
data every second. The animated scanner is visible while a market scan is in
progress.

Qualified signals are automatically routed to MT5 only when the connected
account is confirmed as a demo account and every frozen V12 risk gate passes.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import webbrowser
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.execution import pip_size
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.v12_final_adapter import FinalV12Adapter
from mt5_ai_bridge.v12_final_risk import (
    ADAPTIVE_ENGINES,
    ALLOWED_SYMBOLS,
    DISABLED_ENGINES,
    ENGINE_RULES,
    BacktestExactLimits,
    ResearchSafetyLimits,
)
from v12_final_runner import PROPOSAL_LOG, STATE_FILE, scan_once

ROOT = Path(__file__).resolve().parent
DASHBOARD_STATE = ROOT / "v12_final_dashboard_state.json"
DEFAULT_PORT = 8800


class SharedStatus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._payload: dict[str, Any] = {
            "running": True,
            "scanning": False,
            "last_scan_started": None,
            "last_scan_completed": None,
            "last_error": None,
            "scan_count": 0,
            "new_executions": 0,
            "account": {},
            "positions": [],
            "symbols": [],
            "engines": [],
            "recent_proposals": [],
            "execution_mode": "DEMO_AUTO",
        }

    def update(self, **values: Any) -> None:
        with self._lock:
            self._payload.update(values)
            self._payload["server_time_utc"] = datetime.now(timezone.utc).isoformat()
            temporary = DASHBOARD_STATE.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self._payload, indent=2, default=str), encoding="utf-8")
            temporary.replace(DASHBOARD_STATE)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._payload, default=str))


def _recent_proposals(limit: int = 20) -> list[dict]:
    if not PROPOSAL_LOG.exists():
        return []
    try:
        lines = PROPOSAL_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict] = []
    for line in reversed(lines[-limit:]):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _engine_rows() -> list[dict]:
    rows = []
    for engine, rule in sorted(ENGINE_RULES.items()):
        rows.append({
            "engine": engine,
            "symbol": rule.symbol,
            "adaptive": bool(rule.adaptive),
            "risk_tiers": list(rule.allowed_risk_percent),
            "setups": list(rule.allowed_setups),
            "status": "ADAPTIVE" if engine in ADAPTIVE_ENGINES else "PROTECTED",
        })
    for engine in sorted(DISABLED_ENGINES):
        rows.append({
            "engine": engine,
            "symbol": engine.split("_", 1)[0],
            "adaptive": False,
            "risk_tiers": [],
            "setups": [],
            "status": "DISABLED",
        })
    return rows


def _market_snapshot(client) -> tuple[dict, list[dict], list[dict]]:
    account = client.account_info()
    positions = list(client.positions_get() or [])
    account_row = {}
    if account is not None:
        account_row = {
            "login": getattr(account, "login", None),
            "server": getattr(account, "server", ""),
            "balance": float(getattr(account, "balance", 0.0) or 0.0),
            "equity": float(getattr(account, "equity", 0.0) or 0.0),
            "profit": float(getattr(account, "profit", 0.0) or 0.0),
            "margin": float(getattr(account, "margin", 0.0) or 0.0),
            "free_margin": float(getattr(account, "margin_free", 0.0) or 0.0),
        }
    position_rows = [
        {
            "ticket": int(getattr(p, "ticket", 0) or 0),
            "symbol": str(getattr(p, "symbol", "")),
            "side": "BUY" if int(getattr(p, "type", 1)) == int(getattr(client, "POSITION_TYPE_BUY", 0)) else "SELL",
            "volume": float(getattr(p, "volume", 0.0) or 0.0),
            "open": float(getattr(p, "price_open", 0.0) or 0.0),
            "current": float(getattr(p, "price_current", 0.0) or 0.0),
            "sl": float(getattr(p, "sl", 0.0) or 0.0),
            "tp": float(getattr(p, "tp", 0.0) or 0.0),
            "profit": float(getattr(p, "profit", 0.0) or 0.0),
            "magic": int(getattr(p, "magic", 0) or 0),
            "comment": str(getattr(p, "comment", "")),
        }
        for p in positions
    ]
    symbol_rows = []
    for symbol in sorted(ALLOWED_SYMBOLS):
        tick = client.symbol_info_tick(symbol)
        pip = pip_size(client, symbol)
        if tick is None or pip is None:
            symbol_rows.append({"symbol": symbol, "available": False})
            continue
        bid = float(tick.bid)
        ask = float(tick.ask)
        symbol_rows.append({
            "symbol": symbol,
            "available": True,
            "bid": bid,
            "ask": ask,
            "spread_pips": round((ask - bid) / pip, 2),
        })
    return account_row, position_rows, symbol_rows


def scanner_loop(client, adapter: FinalV12Adapter, status: SharedStatus,
                 interval: float, lookback_hours: int, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        started = datetime.now(timezone.utc)
        status.update(scanning=True, last_scan_started=started.isoformat(), last_error=None)
        try:
            emitted = scan_once(
                client=client,
                adapter=adapter,
                state_path=STATE_FILE,
                proposal_log=PROPOSAL_LOG,
                lookback_hours=lookback_hours,
            )
            account, positions, symbols = _market_snapshot(client)
            current = status.snapshot()
            status.update(
                scanning=False,
                last_scan_completed=datetime.now(timezone.utc).isoformat(),
                scan_count=int(current.get("scan_count", 0)) + 1,
                new_executions=len(emitted),
                account=account,
                positions=positions,
                symbols=symbols,
                engines=_engine_rows(),
                recent_proposals=_recent_proposals(),
            )
        except Exception as exc:  # noqa: BLE001
            status.update(
                scanning=False,
                last_scan_completed=datetime.now(timezone.utc).isoformat(),
                last_error=f"{type(exc).__name__}: {exc}",
                recent_proposals=_recent_proposals(),
                engines=_engine_rows(),
            )
        stop_event.wait(max(1.0, float(interval)))


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>V12 Final Strategy Dashboard</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#090f1f;color:#e7edf7;font-family:Segoe UI,Arial,sans-serif}.wrap{max-width:1280px;margin:auto;padding:20px}.top{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}.title{font-size:24px;font-weight:750}.sub{color:#91a3c1;font-size:13px}.badge{padding:7px 12px;border-radius:999px;font-size:12px;font-weight:750}.green{background:#12351f;color:#39dda0;border:1px solid #1d5b3a}.amber{background:#3b3012;color:#ffd166;border:1px solid #67531c}.red{background:#431b25;color:#ff8397;border:1px solid #713040}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin:18px 0}.card,.panel{background:#121b31;border:1px solid #223151;border-radius:12px;padding:15px}.k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#89a0c2}.v{font-size:22px;font-weight:750;margin-top:4px}.panel{margin-top:14px}h2{font-size:13px;text-transform:uppercase;color:#91a3c1;letter-spacing:.07em}.scanner{display:flex;align-items:center;gap:14px;padding:15px;background:#0d162b;border:1px solid #273a62;border-radius:10px}.spinner{width:34px;height:34px;border-radius:50%;border:4px solid #26385d;border-top-color:#39dda0;animation:spin .75s linear infinite}.spinner.idle{animation:none;border-top-color:#64748b}@keyframes spin{to{transform:rotate(360deg)}}.dots::after{content:'';animation:dots 1.2s steps(4,end) infinite}@keyframes dots{25%{content:'.'}50%{content:'..'}75%,100%{content:'...'}}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:8px;border-bottom:1px solid #223151;white-space:nowrap}.table{overflow:auto}.ok{color:#39dda0}.warn{color:#ffd166}.bad{color:#ff8397}.muted{color:#8494ae}.enginegrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:10px}.engine{padding:12px;background:#0d162b;border:1px solid #223151;border-radius:9px}.pulse{display:inline-block;width:8px;height:8px;border-radius:50%;background:#39dda0;margin-right:7px;box-shadow:0 0 0 0 rgba(57,221,160,.7);animation:pulse 1.5s infinite}@keyframes pulse{70%{box-shadow:0 0 0 11px rgba(57,221,160,0)}}@media(max-width:600px){.wrap{padding:12px}.title{font-size:19px}.v{font-size:18px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">V12 Final Strategy Dashboard</div><div class="sub">Five-symbol completed-candle scanner · one-second dashboard refresh</div></div><span id="mode" class="badge green">DEMO AUTO</span></div>
<div id="scanner" class="scanner" style="margin-top:16px"><div id="spin" class="spinner"></div><div><b id="scanTitle">Strategy engine scanning market<span class="dots"></span></b><div id="scanSub" class="sub">Preparing first scan</div></div></div>
<div class="grid" id="cards"></div>
<h2>Market watch</h2><div class="panel table"><table><thead><tr><th>Symbol</th><th>Bid</th><th>Ask</th><th>Spread</th><th>Status</th></tr></thead><tbody id="symbols"></tbody></table></div>
<h2>Strategy engines</h2><div id="engines" class="enginegrid"></div>
<h2>Open MT5 positions</h2><div class="panel table"><table><thead><tr><th>Ticket</th><th>Symbol</th><th>Side</th><th>Lots</th><th>Open</th><th>Current</th><th>SL</th><th>TP</th><th>P/L</th></tr></thead><tbody id="positions"></tbody></table></div>
<h2>Recent execution attempts</h2><div class="panel table"><table><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Engine</th><th>Setup</th><th>Volume</th><th>Risk</th><th>Ticket</th><th>Result</th></tr></thead><tbody id="proposals"></tbody></table></div>
<div id="error" class="panel bad" style="display:none"></div>
</div><script>
const esc=x=>String(x??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const money=x=>x==null?'—':Number(x).toFixed(2);
function card(k,v,s=''){return `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="sub">${esc(s)}</div></div>`}
async function poll(){try{let r=await fetch('/data?x='+Date.now(),{cache:'no-store'});let d=await r.json();
let a=d.account||{};document.getElementById('cards').innerHTML=card('Balance','$'+money(a.balance),a.server||'')+card('Equity','$'+money(a.equity),'Open P/L $'+money(a.profit))+card('Open positions',(d.positions||[]).length,'Maximum 5')+card('Scans',d.scan_count||0,'1-second cycle')+card('Execution attempts',d.new_executions||0,'Latest scan')+card('Last completed',d.last_scan_completed||'—','UTC');
let scanning=!!d.scanning;document.getElementById('spin').className='spinner'+(scanning?'':' idle');document.getElementById('scanTitle').innerHTML=scanning?'Strategy engine scanning market<span class="dots"></span>':'Waiting for next one-second scan';document.getElementById('scanSub').textContent=scanning?'Evaluating GBPUSD, EURUSD, GBPJPY, AUDUSD and USDJPY':('Last completed: '+(d.last_scan_completed||'not yet'));
document.getElementById('symbols').innerHTML=(d.symbols||[]).map(s=>`<tr><td>${esc(s.symbol)}</td><td>${s.available?esc(s.bid):'—'}</td><td>${s.available?esc(s.ask):'—'}</td><td>${s.available?esc(s.spread_pips)+' p':'—'}</td><td class="${s.available?'ok':'bad'}">${s.available?'LIVE':'UNAVAILABLE'}</td></tr>`).join('');
document.getElementById('engines').innerHTML=(d.engines||[]).map(e=>`<div class="engine"><div class="k">${esc(e.symbol)}</div><b>${esc(e.engine)}</b><div class="${e.status==='DISABLED'?'bad':e.status==='ADAPTIVE'?'warn':'ok'}">${esc(e.status)}</div><div class="sub">Risk tiers: ${esc((e.risk_tiers||[]).join(', ')||'none')} · ${esc((e.setups||[]).join(', ')||'disabled')}</div></div>`).join('');
document.getElementById('positions').innerHTML=(d.positions||[]).map(p=>`<tr><td>${p.ticket}</td><td>${esc(p.symbol)}</td><td>${esc(p.side)}</td><td>${p.volume}</td><td>${p.open}</td><td>${p.current}</td><td>${p.sl||'—'}</td><td>${p.tp||'—'}</td><td class="${p.profit>=0?'ok':'bad'}">${money(p.profit)}</td></tr>`).join('')||'<tr><td colspan="9" class="muted">No open positions.</td></tr>';
document.getElementById('proposals').innerHTML=(d.recent_proposals||[]).map(x=>{let s=x.signal||{},z=x.result||{},p=z.proposal||{};return `<tr><td>${esc(x.created_at||'')}</td><td>${esc(s.symbol)}</td><td>${esc(s.side)}</td><td>${esc(s.engine)}</td><td>${esc(s.setup)}</td><td>${esc(z.volume??p.volume??'—')}</td><td>${esc(z.risk_percent??p.risk_percent??'—')}</td><td>${esc(z.ticket??'—')}</td><td>${esc(z.code||'')}</td></tr>`}).join('')||'<tr><td colspan="9" class="muted">No execution attempts yet.</td></tr>';
let er=document.getElementById('error');if(d.last_error){er.style.display='block';er.textContent=d.last_error}else er.style.display='none';}catch(e){let er=document.getElementById('error');er.style.display='block';er.textContent='Dashboard update failed: '+e}setTimeout(poll,1000)}poll();
</script></body></html>'''


def make_handler(status: SharedStatus):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/data":
                body = json.dumps(status.snapshot(), default=str).encode()
                content_type = "application/json; charset=utf-8"
            elif path in {"/", "/index.html", "/dashboard.html"}:
                body = HTML.encode()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V12 scanner with live dashboard")
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT", DEFAULT_PORT)))
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--lookback-hours", type=int, default=8)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    client = create_client()
    connect(client, settings)
    status = SharedStatus()
    status.update(engines=_engine_rows())
    adapter = FinalV12Adapter(
        client,
        state_path=os.getenv("V12_FINAL_STATE_PATH", "v12_final_research_state.json"),
        max_deviation_points=int(os.getenv("V12_FINAL_MAX_DEVIATION_POINTS", "10")),
    )
    stop_event = threading.Event()
    worker = threading.Thread(
        target=scanner_loop,
        args=(client, adapter, status, args.interval, args.lookback_hours, stop_event),
        daemon=True,
    )
    worker.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(status))
    url = f"http://127.0.0.1:{args.port}"
    print(f"V12 dashboard: {url}")
    print("Dashboard refresh: 1 second")
    print("Strategy scan interval: 1 second")
    print("Execution mode: automatic demo-only MT5 order submission")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        status.update(running=False, scanning=False)
        server.server_close()
        client.shutdown()


if __name__ == "__main__":
    main()
