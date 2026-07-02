"""Patch dashboard.py so V10 multi-symbol state renders and updates live."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "mt5_ai_bridge" / "dashboard.py"

HELPER = '''\n\ndef _dashboard_engines(thinking: Optional[dict]) -> list[dict]:
    """Normalize legacy engine state and V10 multi-symbol state for the UI."""
    if not thinking:
        return []
    engines = list(thinking.get("engines") or [])
    if engines:
        return engines
    normalized = []
    for item in thinking.get("symbols") or []:
        count = int(item.get("signals", 0) or 0)
        setups = [str(value) for value in (item.get("setups") or [])]
        error = item.get("error")
        broker = item.get("broker_symbol") or item.get("symbol") or "unknown"
        ready = bool(count and not error)
        if error:
            reason = f"Error: {error}"
        elif ready:
            reason = f"{count} completed setup(s): " + ", ".join(setups)
        else:
            reason = f"Scanning {broker}; no completed setup this cycle."
        normalized.append({
            "name": item.get("symbol") or broker,
            "ready": ready,
            "bias": ", ".join(setups) if setups else "NONE",
            "reason": reason,
        })
    return normalized
'''

OLD_HEADER = '''    aligned = bool(thinking.get("aligned"))
    badge_cls = "on" if aligned else "off"
    badge = f"ALIGNED {_esc(thinking.get('bias'))}" if aligned else "WAITING"
    sym = _esc(symbol) if symbol else "the market"
    engines = "".join(
'''

NEW_HEADER = '''    symbol_rows = list(thinking.get("symbols") or [])
    total_signals = sum(int(item.get("signals", 0) or 0) for item in symbol_rows)
    aligned = bool(thinking.get("aligned")) if not symbol_rows else total_signals > 0
    badge_cls = "on" if aligned else "off"
    if symbol_rows:
        badge = f"{total_signals} SIGNAL" + ("S" if total_signals != 1 else "") if total_signals else "SCANNING"
        sym = _esc(", ".join(str(item.get("symbol", "")) for item in symbol_rows if item.get("symbol")))
    else:
        badge = f"ALIGNED {_esc(thinking.get('bias'))}" if aligned else "WAITING"
        sym = _esc(symbol) if symbol else "the market"
    engines = "".join(
'''

OLD_ENGINE_LOOP = '''        for e in thinking.get("engines", [])
'''
NEW_ENGINE_LOOP = '''        for e in _dashboard_engines(thinking)
'''

OLD_JS = '''if(d.thinking){var t=d.thinking;var tb=q('think_badge');
if(tb){tb.textContent=t.aligned?('ALIGNED '+t.bias):'WAITING';tb.className='badge '+(t.aligned?'on':'off');}
setTxt('think_sym','Analyzing '+(d.symbol||'the market')+' — live');
setTxt('think_note',t.note);
var eg=(t.engines||[]).map(function(e){return '<div class="engine"><div class="k">'+
esc(e.name)+'</div><div class="estate '+(e.ready?'ready':'waiting')+'">'+
(e.ready?('READY '+esc(e.bias)):'WAITING')+'</div><div class="ereason">'+
esc(e.reason)+'</div></div>';}).join('');setHTML('engine_grid',eg);
var tf=t.timeframes||[];
if(tf.length){var tr=tf.map(function(v){return '<tr><td>'+esc(v.label)+'</td><td>'+esc(v.tf)+
'</td><td class="'+sigCls(v.signal)+'">'+esc(v.signal)+'</td><td>'+Number(v.confidence).toFixed(2)+
'</td><td>'+esc(v.reason)+'</td></tr>';}).join('');
setHTML('think_table','<div class="tablewrap"><table><thead><tr><th>Read</th><th>Timeframe</th>'+
'<th>Signal</th><th>Conf.</th><th>Why</th></tr></thead><tbody>'+tr+'</tbody></table></div>');}
else setHTML('think_table','<p class="empty">Gathering the first read…</p>');}
'''

NEW_JS = '''if(d.thinking){var t=d.thinking;var tb=q('think_badge');var sy=t.symbols||[];
var total=sy.reduce(function(n,s){return n+Number(s.signals||0);},0);
var ready=sy.length?total>0:!!t.aligned;
if(tb){tb.textContent=sy.length?(total?(total+' SIGNAL'+(total===1?'':'S')):'SCANNING'):
(t.aligned?('ALIGNED '+t.bias):'WAITING');tb.className='badge '+(ready?'on':'off');}
setTxt('think_sym','Analyzing '+(sy.length?sy.map(function(s){return s.symbol;}).join(', '):(d.symbol||'the market'))+' — live');
setTxt('think_note',(t.note||'')+' Last cycle: '+d.time_est);
var engines=t.engines||[];
if(!engines.length&&sy.length){engines=sy.map(function(s){var count=Number(s.signals||0),setups=s.setups||[];
var reason=s.error?('Error: '+s.error):(count?(count+' completed setup(s): '+setups.join(', ')):
('Scanning '+(s.broker_symbol||s.symbol)+'; no completed setup this cycle.'));
return {name:s.symbol,ready:count>0&&!s.error,bias:setups.length?setups.join(', '):'NONE',reason:reason};});}
var eg=engines.map(function(e){return '<div class="engine"><div class="k">'+
esc(e.name)+'</div><div class="estate '+(e.ready?'ready':'waiting')+'">'+
(e.ready?('READY '+esc(e.bias)):'WAITING')+'</div><div class="ereason">'+
esc(e.reason)+'</div></div>';}).join('');setHTML('engine_grid',eg);
var tf=t.timeframes||[];
if(tf.length){var tr=tf.map(function(v){return '<tr><td>'+esc(v.label)+'</td><td>'+esc(v.tf)+
'</td><td class="'+sigCls(v.signal)+'">'+esc(v.signal)+'</td><td>'+Number(v.confidence).toFixed(2)+
'</td><td>'+esc(v.reason)+'</td></tr>';}).join('');
setHTML('think_table','<div class="tablewrap"><table><thead><tr><th>Read</th><th>Timeframe</th>'+
'<th>Signal</th><th>Conf.</th><th>Why</th></tr></thead><tbody>'+tr+'</tbody></table></div>');}
else if(sy.length){setHTML('think_table',rowsTable(['Symbol','Broker symbol','Signals','Setups','Status'],
sy.map(function(s){return [s.symbol,s.broker_symbol||'—',s.signals||0,(s.setups||[]).join(', ')||'—',s.error||'Scanning'];})));}
else setHTML('think_table','<p class="empty">Gathering the first read…</p>');}
'''


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    backup = TARGET.with_suffix(TARGET.suffix + ".before-v10-dashboard-fix")
    if not backup.exists():
        backup.write_text(text, encoding="utf-8")

    if "def _dashboard_engines(" not in text:
        marker = "\ndef _thinking_panel(thinking: Optional[dict], symbol: str = \"\") -> str:\n"
        if marker not in text:
            raise RuntimeError("Could not locate _thinking_panel")
        text = text.replace(marker, HELPER + marker, 1)

    if OLD_HEADER in text:
        text = text.replace(OLD_HEADER, NEW_HEADER, 1)
    if OLD_ENGINE_LOOP in text:
        text = text.replace(OLD_ENGINE_LOOP, NEW_ENGINE_LOOP, 1)
    if OLD_JS in text:
        text = text.replace(OLD_JS, NEW_JS, 1)
    elif "Last cycle: "+"'" not in text and "Last cycle:" not in text:
        raise RuntimeError("Could not locate live dashboard JavaScript block")

    TARGET.write_text(text, encoding="utf-8")
    print("Patched V10 multi-symbol dashboard rendering and live-cycle timestamp.")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
