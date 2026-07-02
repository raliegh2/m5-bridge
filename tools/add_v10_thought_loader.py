from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "mt5_ai_bridge" / "dashboard.py"

CSS = """
.thought-loader{display:flex;align-items:center;gap:14px;padding:18px;margin-bottom:14px;border:1px solid #2a3b63;border-radius:10px;background:#0f1730}
.thought-loader.hidden{display:none}.loader-ring{width:36px;height:36px;border-radius:50%;border:4px solid #26385d;border-top-color:#34d399;animation:spin .8s linear infinite}
.loader-title{font-weight:700}.loader-sub{font-size:12px;color:#9fb0cc;margin-top:4px}.loader-dots::after{content:'';animation:dots 1.2s steps(4,end) infinite}
@keyframes spin{to{transform:rotate(360deg)}}@keyframes dots{25%{content:'.'}50%{content:'..'}75%,100%{content:'...'}}
"""

VARS = """    loader_hidden = "" if (symbol_rows and total_signals == 0) else " hidden"
    loader_symbols = ", ".join(str(x.get("symbol", "")) for x in symbol_rows if x.get("symbol")) or sym
"""

HTML = """        f'<div class="thought-loader{loader_hidden}" id="thought_loader">'
        '<div class="loader-ring"></div><div>'
        '<div class="loader-title">Searching for a qualified setup<span class="loader-dots"></span></div>'
        f'<div class="loader-sub" id="loader_symbols">Scanning {_esc(loader_symbols)} using completed candles.</div>'
        '<div class="loader-sub" id="loader_cycle">Refreshing every second.</div></div></div>'
"""

JS_OLD = "var ready=sy.length?total>0:!!t.aligned;"
JS_NEW = """var ready=sy.length?total>0:!!t.aligned;
var loader=q('thought_loader');if(loader){loader.className='thought-loader'+((sy.length&&total===0)?'':' hidden');}
setTxt('loader_symbols','Scanning '+(sy.length?sy.map(function(s){return s.symbol;}).join(', '):(d.symbol||'the market'))+' using completed candles.');
setTxt('loader_cycle','Last scan: '+d.time_est+' · refreshing every second.');"""

text = PATH.read_text(encoding="utf-8")
backup = PATH.with_suffix(PATH.suffix + ".before-thought-loader")
if not backup.exists():
    backup.write_text(text, encoding="utf-8")
if ".thought-loader{" not in text:
    text = text.replace("@media (max-width:900px)", CSS + "\n@media (max-width:900px)", 1)
if "loader_hidden =" not in text:
    text = text.replace("    return (\n        '<h2>What the bot sees now</h2>'", VARS + "    return (\n        '<h2>What the bot sees now</h2>'", 1)
if "id=\"thought_loader\"" not in text:
    text = text.replace("        '<div class=\"panel think\">'\n        '<div class=\"thinkhead\">'", "        '<div class=\"panel think\">'\n" + HTML + "        '<div class=\"thinkhead\">'", 1)
if "refreshing every second" not in text:
    text = text.replace(JS_OLD, JS_NEW, 1)
PATH.write_text(text, encoding="utf-8")
print("Added animated setup-search loader to the V10 thought panel.")
