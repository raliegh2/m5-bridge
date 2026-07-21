"""HTML dashboard generated from the SQLite journal.

Two rendering paths share one computation (``_compute``):

* ``build_dashboard`` -> the full HTML page (the shell + first paint).
* ``build_dashboard_data`` -> a compact JSON snapshot the running bot writes
  each loop. The page polls ``/data`` once per ``refresh_seconds`` and patches
  its values IN PLACE, so it updates in near-realtime with no full-page reload.

Signal clarity: raw per-timeframe reads are separated from real trades in a
"Signal breakdown" panel (total analyses, raw signals, valid setups, executed
trades, filtered-out setups), and the thinking panel explains WHY each timeframe
is bullish/bearish.

If the page is opened directly as a local file (file://) rather than via the
server, its fetches target http://127.0.0.1:<port> so it still works on the PC.

The layout is fully responsive (phone / tablet / desktop). No external
dependencies and no network: everything is inline CSS/JS + an inline SVG.

CLI (static snapshot from the journal):
    python -m mt5_ai_bridge.dashboard [--db journal.db] [--out dashboard.html]
"""

import argparse
import html
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .journal import Journal


# --------------------------------------------------------------------------
# Time / session helpers
# --------------------------------------------------------------------------

def est_now(now_utc: Optional[datetime] = None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        eastern = now_utc.astimezone(ZoneInfo("America/New_York"))
        return eastern.strftime("%I:%M:%S %p %Z")
    except Exception:
        edt = 3 <= now_utc.month <= 11
        offset = -4 if edt else -5
        eastern = now_utc + timedelta(hours=offset)
        return eastern.strftime("%I:%M:%S %p ") + ("EDT" if edt else "EST")


def session_label(now_utc: Optional[datetime] = None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    h = now_utc.hour
    london = 7 <= h < 16
    newyork = 12 <= h < 21
    tokyo = 0 <= h < 9
    sydney = h >= 21 or h < 6
    if london and newyork:
        return "London/New York overlap"
    if newyork:
        return "New York"
    if london:
        return "London"
    if tokyo:
        return "Tokyo"
    if sydney:
        return "Sydney"
    return "Off-hours"


# --------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------

def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _money(v) -> str:
    return "—" if v is None else f"{v:+.2f}"


def _sig_cls(sig: str) -> str:
    return {"BUY": "buy", "SELL": "sell"}.get(str(sig).upper(), "wait")


def _card(label: str, value, sub: str = "", tone: str = "") -> str:
    sub_html = f'<div class="sub">{_esc(sub)}</div>' if sub else ""
    cls = f"card {tone}".strip()
    return (f'<div class="{cls}"><div class="label">{_esc(label)}</div>'
            f'<div class="value">{_esc(value)}</div>{sub_html}</div>')


def _table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        return '<p class="empty">No rows yet.</p>'
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return (f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def _collapsible(title: str, inner: str, count: Optional[int],
                 panel_id: str, count_id: str) -> str:
    """A tap-to-expand section (native <details>) for secondary tables."""
    badge = f' <span class="count" id="{count_id}">{count}</span>' \
        if count is not None else ""
    return (f'<details class="sec"><summary>{_esc(title)}{badge}</summary>'
            f'<div class="panel" id="{panel_id}">{inner}</div></details>')


def _sparkline(values: List[float], width: int = 720, height: int = 150) -> str:
    if len(values) < 2:
        return '<p class="empty">Not enough data for an equity curve yet.</p>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 8
    n = len(values)

    def x(i):
        return pad + (width - 2 * pad) * i / (n - 1)

    def y(v):
        return pad + (height - 2 * pad) * (1 - (v - lo) / span)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    color = "#16a34a" if values[-1] >= values[0] else "#dc2626"
    area = f"{pad:.1f},{height - pad:.1f} " + pts + f" {x(n - 1):.1f},{height - pad:.1f}"
    return (
        f'<svg viewBox="0 0 {width} {height}" class="spark" '
        f'preserveAspectRatio="none" role="img" aria-label="Equity curve">'
        f'<polygon points="{area}" fill="{color}" fill-opacity="0.08"/>'
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="2" stroke-linejoin="round"/></svg>'
    )


def _tone(v) -> str:
    if v is None:
        return ""
    return "pos" if v >= 0 else "neg"


def _control_bar(control: Optional[dict]) -> str:
    if control is None:
        return ""
    active = bool(control.get("active"))
    status = "ACTIVE" if active else "PAUSED"
    cls = "on" if active else "off"
    return (
        '<div class="control">'
        f'<span class="status {cls}" id="ctl_status">● {status}</span>'
        '<button class="btn start" onclick="ctl(\'/start\')">Start trading</button>'
        '<button class="btn stop" onclick="ctl(\'/stop\')">Pause trading</button>'
        '<span class="hint">opens new trades only while ACTIVE — '
        'existing trades keep trailing either way</span>'
        "<script>function ctl(u){var b=(typeof BASE!=='undefined'&&BASE)||'';"
        "var on=u.indexOf('start')>=0;var cs=document.getElementById('ctl_status');"
        "if(cs){cs.textContent=(on?'● ACTIVE':'● PAUSED');"
        "cs.className='status '+(on?'on':'off');}"
        "fetch(b+u,{method:'POST'}).then(function(){setTimeout(function(){"
        "if(window.__poll){window.__poll()}else{location.reload()}},200)})"
        ".catch(function(){if(cs){cs.textContent='● (retry…)';}})}</script>"
        "</div>"
    )



def _prop_toggle(on: bool) -> str:
    """The Enable/Disable prop-mode switch (POSTs /prop/on|off)."""
    return (
        '<label class="ptoggle" title="Turn prop-firm challenge mode on or off">'
        f'<input type="checkbox" id="prop_switch" onclick="propToggle(this.checked)"'
        f'{" checked" if on else ""}>'
        '<span class="ptrack"><span class="pknob"></span></span>'
        f'<span class="ptlabel" id="prop_switch_label">{"ON" if on else "OFF"}</span>'
        '</label>')


def _prop_inner(prop: Optional[dict]) -> str:
    def bar(label, val, mx, kind, muted):
        pct = min(100.0, max(0.0, 100.0 * val / mx)) if mx else 0.0
        mcls = " muted" if muted else ""
        return (f'<div class="pmetric{mcls}"><div class="pmlabel"><span>{_esc(label)}</span>'
                f'<span class="pmval">{val:.2f}% / {mx:g}%</span></div>'
                f'<div class="pbar {kind}"><div class="pfill" '
                f'style="width:{pct:.0f}%"></div></div></div>')
    on = bool(prop.get("enabled"))
    st = prop.get("status", "OFF")
    cls = {"TRADING": "ok", "DE-RISKED": "warn", "DAILY LIMIT": "bad",
           "MAX DRAWDOWN": "bad", "TARGET HIT": "done", "OFF": "off"}.get(st, "off")
    note = ("" if on else
            '<div class="pofftext">Prop-firm challenge mode is OFF. Flip the '
            'switch to protect a funded-challenge account: the bot will cap '
            'daily loss & drawdown and ease off risk as it nears the limits.</div>')
    return (
        f'<div class="prophead"><span class="pbadge {cls}" id="prop_badge">{_esc(st)}</span>'
        f'<span class="psub">Start ${prop.get("start_balance", 0):,.0f} &middot; '
        f'Equity ${prop.get("equity", 0):,.0f} &middot; '
        f'risk &times;{prop.get("risk_scale", 1)}</span>'
        f'{_prop_toggle(on)}</div>'
        f'{note}'
        + bar("Profit target", prop.get("profit_pct", 0), prop.get("profit_target_pct", 0), "good", not on)
        + bar("Daily loss", prop.get("daily_loss_pct", 0), prop.get("max_daily_loss_pct", 0), "loss", not on)
        + bar("Max drawdown", prop.get("total_dd_pct", 0), prop.get("max_total_loss_pct", 0), "loss", not on)
    )


def _prop_panel(prop: Optional[dict]) -> str:
    # Always render the section (even when OFF) so the toggle is discoverable.
    inner = _prop_inner(prop) if prop else _prop_inner({"enabled": False})
    return ('<h2>Prop challenge <span class="since">pass funded evaluations</span></h2>'
            f'<div class="panel prop" id="prop_panel">{inner}</div>'
            '<script>function propToggle(on){'
            "var b=(typeof BASE!=='undefined'&&BASE)||'';"
            "var l=document.getElementById('prop_switch_label');if(l)l.textContent=on?'ON':'OFF';"
            "fetch(b+(on?'/prop/on':'/prop/off'),{method:'POST'}).then(function(){"
            "setTimeout(function(){if(window.__poll)window.__poll();},200);})"
            ".catch(function(){if(l)l.textContent='(retry…)';});}</script>")


def _engine_breakdown_panel(rows) -> str:
    """Per-symbol block: both engines' state + reason, plus the timeframe reads
    that drove the decision. Shows the full decision process for EVERY pair."""
    if not rows:
        return ""
    def _engine_card(e) -> str:
        enabled = e.get("enabled", True)
        if not enabled:
            state_cls, state_txt = "disabled", "DISABLED"
        elif e.get("ready"):
            state_cls, state_txt = "ready", "READY " + _esc(e.get("bias")) + \
                " &middot; " + f'{float(e.get("confidence", 0)):.2f}'
        else:
            state_cls, state_txt = "waiting", "WAITING"
        risk = f'{float(e.get("risk", 0)):g}%' if "risk" in e else ""
        head = (_esc(e.get("name")) +   # Intraday / Swing (the trade type)
                (f' &middot; risk {risk}' if risk else ""))
        cls = "engine off" if not enabled else "engine"
        return (f'<div class="{cls}"><div class="k">{head}</div>'
                f'<div class="estate {state_cls}">{state_txt}</div>'
                f'<div class="ereason">{_esc(e.get("reason"))}</div></div>')

    blocks = []
    for r in rows:
        sym = _esc(r.get("symbol", ""))
        aligned = bool(r.get("aligned"))
        bcls = "on" if aligned else "off"
        blabel = f"ALIGNED {_esc(r.get('bias'))}" if aligned else "WAITING"
        engines = "".join(_engine_card(e) for e in r.get("engines", []))
        trades = r.get("trades", [])
        trades_txt = (" + ".join(_esc(t) for t in trades) if trades
                      else "none (both engines disabled)")
        reg = r.get("regime") or {}
        reg_chip = ""
        if reg.get("er") is not None:
            held = reg.get("filter_on") and not reg.get("allowed")
            rcls = "bad" if held else ("ok" if reg.get("state") == "directional" else "")
            rtxt = (f'Regime: {_esc(str(reg.get("state", "")).title())} '
                    f'&middot; ER {reg["er"]:.2f}'
                    + (' &middot; standing aside' if held else ''))
            reg_chip = f'<span class="regime {rcls}">{rtxt}</span>'
        tfs = r.get("timeframes", [])
        tf_rows = "".join(
            f'<tr><td>{_esc(v.get("label"))}</td><td>{_esc(v.get("tf"))}</td>'
            f'<td class="{_sig_cls(v.get("signal"))}">{_esc(v.get("signal"))}</td>'
            f'<td>{float(v.get("confidence", 0)):.2f}</td>'
            f'<td>{_esc(v.get("reason"))}</td></tr>'
            for v in tfs
        )
        proc = (f'<details class="sec"><summary>Decision process &mdash; '
                f'timeframe reads</summary><div class="tablewrap"><table><thead>'
                f'<tr><th>Read</th><th>Timeframe</th><th>Signal</th><th>Conf.</th>'
                f'<th>Why</th></tr></thead><tbody>{tf_rows}</tbody></table></div>'
                f'</details>') if tf_rows else ""
        blocks.append(
            f'<div class="symrow"><div class="symhead"><span class="symname">{sym}'
            f'</span><span class="badge {bcls}">{blabel}</span>{reg_chip}'
            f'<span class="trades">Trades: {trades_txt}</span></div>'
            f'<div class="enginegrid">{engines}</div>{proc}</div>')
    return ('<h2>All engines &mdash; decision process '
            '<span class="since">every pair, intraday + swing</span></h2>'
            f'<div class="panel" id="engines_panel">{"".join(blocks)}</div>')


def _thinking_panel(thinking: Optional[dict], symbol: str = "") -> str:
    """The bot's current per-timeframe read + why, with a live indicator."""
    if not thinking:
        return ""
    views = thinking.get("timeframes", [])
    if views:
        rows = "".join(
            f'<tr><td>{_esc(v.get("label"))}</td><td>{_esc(v.get("tf"))}</td>'
            f'<td class="{_sig_cls(v.get("signal"))}">{_esc(v.get("signal"))}</td>'
            f'<td>{float(v.get("confidence", 0)):.2f}</td>'
            f'<td>{_esc(v.get("reason"))}</td></tr>'
            for v in views
        )
        table = (
            '<div class="tablewrap"><table><thead><tr><th>Read</th>'
            '<th>Timeframe</th><th>Signal</th><th>Conf.</th><th>Why</th></tr>'
            f'</thead><tbody>{rows}</tbody></table></div>'
        )
    else:
        table = '<p class="empty">Gathering the first read…</p>'

    aligned = bool(thinking.get("aligned"))
    badge_cls = "on" if aligned else "off"
    badge = f"ALIGNED {_esc(thinking.get('bias'))}" if aligned else "WAITING"
    sym = _esc(symbol) if symbol else "the market"
    engines = "".join(
        '<div class="engine"><div class="k">' + _esc(e.get("name")) +
        '</div><div class="estate ' + ("ready" if e.get("ready") else "waiting") +
        '">' + ("READY " + _esc(e.get("bias")) if e.get("ready") else "WAITING") +
        '</div><div class="ereason">' + _esc(e.get("reason")) + '</div></div>'
        for e in thinking.get("engines", [])
    )
    engine_panel = (f'<div class="enginegrid" id="engine_grid">{engines}</div>'
                    if engines else '<div id="engine_grid"></div>')
    return (
        '<h2>What the bot sees now</h2>'
        '<div class="panel think">'
        '<div class="thinkhead">'
        f'<span class="badge {badge_cls}" id="think_badge">{badge}</span>'
        '<span class="analyzing"><span class="pulse"></span> '
        f'<span id="think_sym">Analyzing {sym} — live</span></span>'
        f'<span class="note" id="think_note">{_esc(thinking.get("note", ""))}</span></div>'
        f'{engine_panel}'
        f'<div id="think_table">{table}</div>'
        '<div class="scan" title="Continuously reading the market"></div>'
        '</div>'
    )


def _signal_breakdown_panel(stats: Optional[dict]) -> str:
    """Separate raw analyses from real trades so nobody mistakes a raw signal
    count (e.g. 'SELL 60') for 60 trade entries."""
    s = stats or {}
    raw = (f"BUY {s.get('raw_buy', 0)} &middot; SELL {s.get('raw_sell', 0)} "
           f"&middot; WAIT {s.get('raw_wait', 0)}")

    def cell(label, vid, value, small=False):
        cls = "v small" if small else "v"
        return (f'<div class="stat"><div class="k">{_esc(label)}</div>'
                f'<div class="{cls}" id="{vid}">{value}</div></div>')

    return (
        '<h2>Signal breakdown <span class="since">today (UTC)</span></h2>'
        '<div class="panel">'
        '<p class="statnote">These are market <b>checks</b>, not trades. A raw '
        'signal only becomes a trade when either the intraday engine (M15/M30) '
        'or swing engine (H4/D1 plus M30/M15 timing) passes its filters.</p>'
        '<div class="statgrid">'
        + cell("Total analyses", "st_analyses", s.get("analyses", 0))
        + cell("Raw timeframe signals", "st_raw", raw, small=True)
        + cell("Valid trade setups", "st_setups", s.get("setups", 0))
        + cell("Executed trades", "st_exec", s.get("executed", 0))
        + cell("Filtered-out setups", "st_filtered", s.get("filtered", 0))
        + '</div></div>'
    )


# --------------------------------------------------------------------------
# Live position view
# --------------------------------------------------------------------------

def _position_view(pos: dict, pip_size: float) -> dict:
    side = pos.get("type")
    entry = pos.get("price_open")
    cur = pos.get("price_current")
    sl = pos.get("sl") or 0.0
    tp = pos.get("tp") or 0.0
    direction = 1.0 if side == "BUY" else -1.0

    # Prefer the position's OWN pip size (gold/JPY/FX differ); fall back to the
    # account-level pip size only when a per-position value is absent.
    ps = pos.get("pip_size") or pip_size

    pips = None
    if entry and cur and ps:
        pips = round(direction * (cur - entry) / ps, 1)

    rr = None
    if entry and sl and tp:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk > 0:
            rr = round(reward / risk, 2)

    return {
        "symbol": pos.get("symbol"), "ticket": pos.get("ticket"),
        "side": side, "volume": pos.get("volume"),
        "entry": entry, "current": cur, "pips": pips, "profit": pos.get("profit"),
        "sl": sl or None, "tp": tp or None, "rr": rr,
    }


# --------------------------------------------------------------------------
# Shared computation (one source of truth for HTML + JSON)
# --------------------------------------------------------------------------

def _compute(journal: Journal, live: Optional[dict], now_utc: datetime) -> dict:
    signals = journal.recent_signals(60)
    orders = journal.recent_orders(60)
    risk = journal.recent_risk_events(300)

    equity_series = [r["equity"] for r in reversed(risk) if r["equity"] is not None]
    latest_risk = risk[0] if risk else None

    balance = live["balance"] if live else (latest_risk["balance"] if latest_risk else None)
    equity = live["equity"] if live else (latest_risk["equity"] if latest_risk else None)
    pip_size = (live or {}).get("pip_size") or 0.0001
    symbol = (live or {}).get("symbol", "")

    positions = [_position_view(p, pip_size) for p in (live or {}).get("positions", [])]
    open_pl = sum(p["profit"] for p in positions if p["profit"] is not None) \
        if positions else (round(equity - balance, 2)
                           if (equity is not None and balance is not None) else None)

    day_start = journal.day_start_equity()
    day_pl = round(equity - day_start, 2) \
        if (equity is not None and day_start is not None) else None

    rr_values = [p["rr"] for p in positions if p["rr"] is not None]
    rr_headline = rr_values[0] if rr_values else None

    return {
        "now_utc": now_utc, "symbol": symbol, "pip_size": pip_size,
        "balance": balance, "equity": equity, "open_pl": open_pl, "day_pl": day_pl,
        "rr_headline": rr_headline, "positions": positions,
        "signals": signals, "orders": orders, "equity_series": equity_series,
        "latest_risk": latest_risk,
        "signal_stats": journal.signal_stats_today(),
        "signal_rows": [[s["ts"], s["symbol"], s["signal"], s["reason"]]
                        for s in signals[:20]],
        "order_rows": [[o["ts"], o["symbol"], o["side"], o["volume"], o["ticket"],
                        o["status"], o["message"]] for o in orders[:20]],
    }


def build_dashboard_data(journal: Journal, live: Optional[dict] = None,
                         refresh_seconds: int = 1,
                         now_utc: Optional[datetime] = None,
                         control: Optional[dict] = None,
                         thinking: Optional[dict] = None,
                         prop: Optional[dict] = None,
                         engines: Optional[list] = None) -> dict:
    """The JSON snapshot the page polls for in-place live updates."""
    now_utc = now_utc or datetime.now(timezone.utc)
    c = _compute(journal, live, now_utc)
    return {
        "live": live is not None,
        "time_est": est_now(now_utc),
        "session": session_label(now_utc),
        "symbol": c["symbol"],
        "symbols": (live or {}).get("symbols", [c["symbol"]]),
        "pip_size": c["pip_size"],
        "refresh_seconds": refresh_seconds,
        "control": control,
        "thinking": thinking,
        "prop": prop,
        "engines_by_symbol": engines or [],
        "cards": {
            "open_pl": c["open_pl"], "day_pl": c["day_pl"],
            "rr": c["rr_headline"], "balance": c["balance"], "equity": c["equity"],
            "open_positions": len(c["positions"]) if live else
            (c["latest_risk"]["open_positions"] if c["latest_risk"] else None),
        },
        "signal_stats": c["signal_stats"],
        "positions": c["positions"],
        "signals": c["signal_rows"],
        "orders": c["order_rows"],
        "signals_count": len(c["signals"]),
        "orders_count": len(c["orders"]),
        "equity_series": c["equity_series"],
    }


_CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
margin:0 auto;padding:24px;background:#0b1020;color:#e7ecf3;max-width:1100px}
h1{font-size:20px;margin:0 0 2px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#93a4bd;
margin:26px 0 10px}
.since{font-size:10px;color:#5f6b84;text-transform:none;letter-spacing:0}
.meta{color:#9fb0cc;font-size:13px;margin-bottom:14px}
.meta b{color:#e7ecf3}
.control{display:flex;align-items:center;gap:12px;background:#141c33;
border:1px solid #223052;border-radius:10px;padding:12px 14px;margin-bottom:16px;flex-wrap:wrap}
.control .status{font-weight:700;font-size:14px}
.control .status.on{color:#34d399}.control .status.off{color:#fbbf24}
.control .hint{color:#7e8aa3;font-size:12px;margin-left:auto}
.btn{border:0;border-radius:8px;padding:10px 16px;font-weight:650;cursor:pointer;
font-size:13px;flex:1 1 auto;min-width:130px}
.btn.start{background:#16a34a;color:#fff}.btn.stop{background:#dc2626;color:#fff}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:#141c33;border:1px solid #223052;border-radius:10px;padding:14px}
.card.pos{border-color:#1f5132}.card.neg{border-color:#5b2330}
.card .label{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#8aa0c0}
.card .value{font-size:22px;font-weight:650;margin-top:4px}
.card.pos .value{color:#34d399}.card.neg .value{color:#f87171}
.card .sub{font-size:11px;color:#7e8aa3;margin-top:2px}
.panel{background:#141c33;border:1px solid #223052;border-radius:10px;padding:16px;margin-top:10px}
.think .thinkhead{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.badge{font-weight:700;font-size:12px;padding:5px 12px;border-radius:999px;letter-spacing:.03em}
.badge.on{background:#12351f;color:#34d399;border:1px solid #1f5132}
.badge.off{background:#3a2f12;color:#fbbf24;border:1px solid #5c4a17}
.think .note{color:#b7c2d8;font-size:13px}
.enginegrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin:0 0 14px}
.engine{background:#0f1730;border:1px solid #223052;border-radius:8px;padding:10px 12px}
.engine .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#8aa0c0}
.estate{font-size:14px;font-weight:700;margin-top:3px}.estate.ready{color:#34d399}.estate.waiting{color:#fbbf24}
.ereason{font-size:11px;color:#9fb0cc;margin-top:3px}
.analyzing{color:#8fe3c0;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:7px}
.pulse{display:inline-block;width:9px;height:9px;border-radius:50%;background:#34d399;
box-shadow:0 0 0 0 rgba(52,211,153,.7);animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(52,211,153,.55)}
70%{box-shadow:0 0 0 11px rgba(52,211,153,0)}100%{box-shadow:0 0 0 0 rgba(52,211,153,0)}}
.scan{position:relative;height:3px;border-radius:3px;background:#1a2440;overflow:hidden;margin-top:12px}
.scan::after{content:"";position:absolute;top:0;left:-40%;width:40%;height:100%;
background:linear-gradient(90deg,transparent,#34d399,transparent);animation:scan 1.5s linear infinite}
@keyframes scan{0%{left:-40%}100%{left:100%}}
.statnote{color:#b7c2d8;font-size:12.5px;margin:0 0 12px}
.statgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stat{background:#0f1730;border:1px solid #223052;border-radius:8px;padding:10px 12px}
.stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#8aa0c0}
.stat .v{font-size:20px;font-weight:650;margin-top:3px}
.stat .v.small{font-size:14px;font-weight:600}
td.buy{color:#34d399;font-weight:600}td.sell{color:#f87171;font-weight:600}td.wait{color:#9aa6bd}
.spark{width:100%;height:auto;display:block}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid #223052;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis;max-width:360px}
th{color:#93a4bd;font-weight:600}
tbody tr:hover{background:#1a2440}
.empty{color:#7e8aa3;font-size:13px;margin:6px 0}
details.sec{margin-top:24px;border-top:1px solid #1a2440;padding-top:6px}
details.sec>summary{font-size:13px;text-transform:uppercase;letter-spacing:.06em;
color:#93a4bd;font-weight:600;cursor:pointer;list-style:none;display:flex;
align-items:center;gap:8px;padding:8px 0}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary::before{content:"\\25B8";color:#5f6b84;transition:transform .15s;
display:inline-block}
details.sec[open]>summary::before{transform:rotate(90deg)}
details.sec .count{background:#223052;color:#9fb0cc;border-radius:999px;
font-size:11px;padding:1px 8px;letter-spacing:0}
details.sec .panel{margin-top:4px}
.live{display:inline-block;width:8px;height:8px;border-radius:50%;background:#34d399;
margin-right:6px;vertical-align:middle}
.foot{color:#5f6b84;font-size:11px;margin-top:28px}
.foot.warn{color:#fbbf24;font-weight:600}
@media (max-width:900px){h1{font-size:18px}.card .value{font-size:20px}}
@media (max-width:600px){
body{padding:14px}
h1{font-size:17px}
h2{margin:20px 0 8px}
.meta{font-size:12px}
.cards{grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:8px}
.card{padding:11px}
.card .label{font-size:10px}
.card .value{font-size:18px}
.panel{padding:12px}
.control{gap:8px;padding:10px}
.control .status{flex:1 1 100%}
.control .hint{margin-left:0;flex:1 1 100%}
th,td{padding:6px 7px;font-size:12px;max-width:150px}
}
@media (prefers-reduced-motion:reduce){
.pulse{animation:none}.scan::after{animation:none;left:0;width:100%;opacity:.25}
}
.prop .prophead{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.pbadge{font-weight:700;font-size:12px;padding:5px 12px;border-radius:999px;letter-spacing:.03em}
.pbadge.ok{background:#12351f;color:#34d399;border:1px solid #1f5132}
.pbadge.warn{background:#3a2f12;color:#fbbf24;border:1px solid #5c4a17}
.pbadge.bad{background:#3a1620;color:#f87171;border:1px solid #5b2330}
.pbadge.done{background:#122f3a;color:#38bdf8;border:1px solid #17495c}
.prop .psub{color:#9fb0cc;font-size:12px}
.pmetric{margin:10px 0}
.pmlabel{display:flex;justify-content:space-between;font-size:12px;color:#b7c2d8;margin-bottom:5px}
.pmval{color:#8aa0c0}
.pbar{height:11px;border-radius:6px;background:#0f1730;border:1px solid #223052;overflow:hidden}
.pbar .pfill{height:100%;border-radius:6px;transition:width .4s ease}
.pbar.good .pfill{background:linear-gradient(90deg,#16a34a,#34d399)}
.pbar.loss .pfill{background:linear-gradient(90deg,#f59e0b,#ef4444)}
.pbadge.off{background:#1a2440;color:#8aa0c0;border:1px solid #2c3a5c}
.pmetric.muted{opacity:.4}
.pofftext{color:#9fb0cc;font-size:12.5px;margin:2px 0 14px;line-height:1.5}
.ptoggle{display:inline-flex;align-items:center;gap:8px;cursor:pointer;margin-left:auto;user-select:none}
.ptoggle input{position:absolute;opacity:0;width:0;height:0}
.ptrack{position:relative;width:42px;height:23px;border-radius:999px;background:#2c3a5c;transition:background .2s}
.pknob{position:absolute;top:2px;left:2px;width:19px;height:19px;border-radius:50%;background:#e7ecf3;transition:transform .2s}
.ptoggle input:checked+.ptrack{background:#16a34a}
.ptoggle input:checked+.ptrack .pknob{transform:translateX(19px)}
.ptlabel{font-size:12px;font-weight:700;color:#93a4bd;min-width:26px}
.ptoggle input:checked~.ptlabel{color:#34d399}
.symrow{border:1px solid #223052;border-radius:10px;padding:12px;margin:0 0 12px;background:#0f1730}
.symhead{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.symname{font-size:15px;font-weight:700;letter-spacing:.02em}
.symrow .enginegrid{margin:0 0 8px}
.symrow details.sec{margin-top:6px;border-top:1px solid #1a2440;padding-top:2px}
.symrow details.sec>summary{font-size:11px;padding:6px 0}
.engine.off{opacity:.55}
.estate.disabled{color:#7e8aa3;font-weight:700}
.trades{font-size:11px;color:#8aa0c0;margin-left:auto;white-space:nowrap}
.trades{font-weight:600}
.regime{font-size:11px;font-weight:600;padding:3px 9px;border-radius:999px;
background:#1a2440;color:#9fb0cc;border:1px solid #2c3a5c}
.regime.ok{background:#12351f;color:#34d399;border-color:#1f5132}
.regime.bad{background:#3a1620;color:#f87171;border-color:#5b2330}
"""


# In-place live updater. Plain (non-f) string: fetches BASE+/data every RS
# seconds and patches values by id — no full-page reload.
_LIVE_JS = r"""
function q(i){return document.getElementById(i);}
function setTxt(i,v){var e=q(i);if(e)e.textContent=(v==null?'':v);}
function setHTML(i,v){var e=q(i);if(e)e.innerHTML=v;}
function foot(msg,warn){var e=q('foot_state');if(e){e.textContent=msg;
e.className='foot'+(warn?' warn':'');}}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function money(v){return v==null?'—':((v>=0?'+':'')+Number(v).toFixed(2));}
function toneOf(v){return v==null?'':(v>=0?'pos':'neg');}
function sigCls(s){s=String(s).toUpperCase();return s=='BUY'?'buy':(s=='SELL'?'sell':'wait');}
function cardEl(label,value,sub,tone){return '<div class="card '+(tone||'')+
'"><div class="label">'+esc(label)+'</div><div class="value">'+value+'</div>'+
(sub?'<div class="sub">'+esc(sub)+'</div>':'')+'</div>';}
function rowsTable(head,rows){
if(!rows||!rows.length)return '<p class="empty">No rows yet.</p>';
var h='<div class="tablewrap"><table><thead><tr>';
head.forEach(function(x){h+='<th>'+esc(x)+'</th>';});
h+='</tr></thead><tbody>';
rows.forEach(function(r){h+='<tr>';r.forEach(function(c){h+='<td>'+esc(c)+'</td>';});h+='</tr>';});
return h+'</tbody></table></div>';}
function posTable(ps){
if(!ps||!ps.length)return '<p class="empty">No rows yet.</p>';
var rows=ps.map(function(p){return [p.symbol||'—',p.ticket,p.side,p.volume,
p.entry!=null?p.entry.toFixed(5):'—',
p.current!=null?p.current.toFixed(5):'—',
p.pips!=null?((p.pips>=0?'+':'')+p.pips.toFixed(1)):'—',
money(p.profit),p.sl?p.sl.toFixed(5):'—',p.tp?p.tp.toFixed(5):'—',
p.rr?('1:'+p.rr):'—'];});
return rowsTable(['Symbol','Ticket','Side','Vol','Entry','Current','Pips','P/L','SL','TP','R:R'],rows);}
function persymTable(ps,syms){var a={},ord=[];(syms||[]).forEach(function(s){a[s]=[0,0];ord.push(s);});
(ps||[]).forEach(function(p){var s=p.symbol||'?';if(!(s in a)){a[s]=[0,0];ord.push(s);}a[s][0]++;a[s][1]+=(p.profit||0);});
if(!ord.length)return '<p class="empty">No symbols.</p>';
var rows=ord.map(function(s){return [s,a[s][0],money(a[s][1])];});
return rowsTable(['Symbol','Open','P/L'],rows);}
function propInner(pr){
var on=!!pr.enabled;
function bar(label,val,mx,kind){var pct=mx?Math.max(0,Math.min(100,100*val/mx)):0;
return '<div class="pmetric'+(on?'':' muted')+'"><div class="pmlabel"><span>'+esc(label)+'</span><span class="pmval">'+
Number(val).toFixed(2)+'% / '+mx+'%</span></div><div class="pbar '+kind+
'"><div class="pfill" style="width:'+pct.toFixed(0)+'%"></div></div></div>';}
var st=pr.status||'OFF';var cls=({'TRADING':'ok','DE-RISKED':'warn','DAILY LIMIT':'bad','MAX DRAWDOWN':'bad','TARGET HIT':'done','OFF':'off'})[st]||'off';
var toggle='<label class="ptoggle" title="Turn prop-firm challenge mode on or off">'+
'<input type="checkbox" id="prop_switch" onclick="propToggle(this.checked)"'+(on?' checked':'')+'>'+
'<span class="ptrack"><span class="pknob"></span></span>'+
'<span class="ptlabel" id="prop_switch_label">'+(on?'ON':'OFF')+'</span></label>';
var note=on?'':'<div class="pofftext">Prop-firm challenge mode is OFF. Flip the switch to protect a funded-challenge account: the bot will cap daily loss & drawdown and ease off risk as it nears the limits.</div>';
return '<div class="prophead"><span class="pbadge '+cls+'" id="prop_badge">'+esc(st)+'</span><span class="psub">Start $'+
Number(pr.start_balance).toFixed(0)+' · Equity $'+Number(pr.equity).toFixed(0)+' · risk ×'+pr.risk_scale+
'</span>'+toggle+'</div>'+note+bar('Profit target',pr.profit_pct,pr.profit_target_pct,'good')+
bar('Daily loss',pr.daily_loss_pct,pr.max_daily_loss_pct,'loss')+
bar('Max drawdown',pr.total_dd_pct,pr.max_total_loss_pct,'loss');}
function enginesPanel(rows){
if(!rows||!rows.length)return '<p class="empty">Waiting for the first read…</p>';
return rows.map(function(r){
var aligned=!!r.aligned;var blabel=aligned?('ALIGNED '+esc(r.bias)):'WAITING';
var eng=(r.engines||[]).map(function(e){
var enabled=(e.enabled!==false);
var scls,stxt;
if(!enabled){scls='disabled';stxt='DISABLED';}
else if(e.ready){scls='ready';stxt='READY '+esc(e.bias)+' · '+Number(e.confidence).toFixed(2);}
else{scls='waiting';stxt='WAITING';}
var risk=(e.risk!=null)?(' · risk '+e.risk+'%'):'';
return '<div class="engine'+(enabled?'':' off')+'"><div class="k">'+esc(e.name)+risk+
'</div><div class="estate '+scls+'">'+stxt+'</div><div class="ereason">'+esc(e.reason)+
'</div></div>';}).join('');
var trades=(r.trades&&r.trades.length)?r.trades.map(esc).join(' + '):'none (both engines disabled)';
var reg=r.regime||{};var regChip='';
if(reg.er!=null){var held=reg.filter_on&&!reg.allowed;
var rcls=held?'bad':(reg.state=='directional'?'ok':'');
regChip='<span class="regime '+rcls+'">Regime: '+esc((reg.state||'')[0]?((reg.state||'').charAt(0).toUpperCase()+(reg.state||'').slice(1)):'')+' · ER '+Number(reg.er).toFixed(2)+(held?' · standing aside':'')+'</span>';}
var tf=r.timeframes||[];var proc='';
if(tf.length){var tr=tf.map(function(v){return '<tr><td>'+esc(v.label)+'</td><td>'+esc(v.tf)+
'</td><td class="'+sigCls(v.signal)+'">'+esc(v.signal)+'</td><td>'+Number(v.confidence).toFixed(2)+
'</td><td>'+esc(v.reason)+'</td></tr>';}).join('');
proc='<details class="sec"><summary>Decision process — timeframe reads</summary>'+
'<div class="tablewrap"><table><thead><tr><th>Read</th><th>Timeframe</th><th>Signal</th>'+
'<th>Conf.</th><th>Why</th></tr></thead><tbody>'+tr+'</tbody></table></div></details>';}
return '<div class="symrow"><div class="symhead"><span class="symname">'+esc(r.symbol)+
'</span><span class="badge '+(aligned?'on':'off')+'">'+blabel+'</span>'+regChip+
'<span class="trades">Trades: '+trades+'</span></div>'+
'<div class="enginegrid">'+eng+'</div>'+proc+'</div>';}).join('');}
function spark(vals){
if(!vals||vals.length<2)return '<p class="empty">Not enough data for an equity curve yet.</p>';
var w=720,h=150,pad=8,n=vals.length,lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals),
sp=(hi-lo)||1;
function x(i){return pad+(w-2*pad)*i/(n-1);}
function y(v){return pad+(h-2*pad)*(1-(v-lo)/sp);}
var pts=vals.map(function(v,i){return x(i).toFixed(1)+','+y(v).toFixed(1);}).join(' ');
var col=vals[n-1]>=vals[0]?'#16a34a':'#dc2626';
var area=pad.toFixed(1)+','+(h-pad).toFixed(1)+' '+pts+' '+x(n-1).toFixed(1)+','+(h-pad).toFixed(1);
return '<svg viewBox="0 0 '+w+' '+h+'" class="spark" preserveAspectRatio="none">'+
'<polygon points="'+area+'" fill="'+col+'" fill-opacity="0.08"/>'+
'<polyline points="'+pts+'" fill="none" stroke="'+col+'" stroke-width="2" stroke-linejoin="round"/></svg>';}
function apply(d){
if(!d||!d.live){foot('Waiting for the first live snapshot…',false);return;}
setTxt('m_time',d.time_est);setTxt('m_session',d.session);setTxt('m_symbol',(d.symbols&&d.symbols.length)?d.symbols.join(', '):d.symbol);
if(d.control){var cs=q('ctl_status');if(cs){cs.textContent='● '+(d.control.active?'ACTIVE':'PAUSED');
cs.className='status '+(d.control.active?'on':'off');}}
var c=d.cards,ch='';
ch+=cardEl('Open P/L',money(c.open_pl),'floating',toneOf(c.open_pl));
ch+=cardEl('Day P/L',money(c.day_pl),'since 00:00 UTC',toneOf(c.day_pl));
ch+=cardEl('Risk : Reward',c.rr!=null?('1 : '+c.rr):'—','open position','');
ch+=cardEl('Balance',c.balance!=null?Number(c.balance).toFixed(2):'—','','');
ch+=cardEl('Equity',c.equity!=null?Number(c.equity).toFixed(2):'—','','');
ch+=cardEl('Open positions',c.open_positions==null?'—':c.open_positions,'','');
setHTML('cards',ch);
if(d.signal_stats){var ss=d.signal_stats;
setTxt('st_analyses',ss.analyses);
setTxt('st_raw','BUY '+ss.raw_buy+' · SELL '+ss.raw_sell+' · WAIT '+ss.raw_wait);
setTxt('st_setups',ss.setups);
setTxt('st_exec',ss.executed);
setTxt('st_filtered',ss.filtered);}
if(d.thinking){var t=d.thinking;var tb=q('think_badge');
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
setHTML('pos_panel',posTable(d.positions));
setHTML('persym_panel',persymTable(d.positions,d.symbols));
if(d.prop)setHTML('prop_panel',propInner(d.prop));
if(d.engines_by_symbol)setHTML('engines_panel',enginesPanel(d.engines_by_symbol));
setHTML('eq_panel',spark(d.equity_series));
setHTML('sig_panel',rowsTable(['Time','Symbol','Signal','Reason'],d.signals));
setHTML('ord_panel',rowsTable(['Time','Symbol','Side','Vol','Ticket','Status','Message'],d.orders));
setTxt('sig_count',d.signals_count);setTxt('ord_count',d.orders_count);
foot('Live — updating every '+RS+'s in place (no reload).',false);}
function poll(){fetch(BASE+'/data',{cache:'no-store'}).then(function(r){
if(!r.ok)throw r.status;return r.json();}).then(apply).catch(function(e){
if(location.protocol==='file:'){foot('You opened the dashboard as a FILE. For live '+
'updates, open  http://127.0.0.1:'+PORT+'  in your browser on this PC instead.',true);}
else if(e===404){foot('Live data endpoint missing (/data 404) — an OLDER copy of the '+
'bot is still serving this page. Fully close every bot window and start it once '+
'more, then reload.',true);}
else{foot('Reconnecting to the bot…',true);}});}
window.__poll=poll;poll();setInterval(poll,RS*1000);
if(window.innerWidth>600){var dd=document.querySelectorAll('details.sec');
for(var i=0;i<dd.length;i++){dd[i].open=true;}}
"""



def _per_symbol_rows(positions, symbols) -> list:
    """[symbol, open count, total P/L] per traded symbol."""
    agg = {}
    for p in positions:
        sym = p.get("symbol") or "?"
        cnt, pl = agg.get(sym, (0, 0.0))
        agg[sym] = (cnt + 1, pl + (p.get("profit") or 0.0))
    order = list(dict.fromkeys(list(symbols) + list(agg.keys())))
    return [[s, agg.get(s, (0, 0.0))[0], _money(round(agg.get(s, (0, 0.0))[1], 2))]
            for s in order]


def build_dashboard(journal: Journal, live: Optional[dict] = None,
                    refresh_seconds: int = 0, now_utc: Optional[datetime] = None,
                    control: Optional[dict] = None,
                    thinking: Optional[dict] = None, port: int = 8800,
                    prop: Optional[dict] = None,
                    engines: Optional[list] = None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    c = _compute(journal, live, now_utc)
    balance, equity = c["balance"], c["equity"]
    pip_size, symbol = c["pip_size"], c["symbol"]
    symbols = (live or {}).get("symbols", [symbol])
    positions, latest_risk = c["positions"], c["latest_risk"]
    live_dot = '<span class="live"></span>' if live else ""

    cards = "".join([
        _card("Open P/L", _money(c["open_pl"]), "floating", _tone(c["open_pl"])),
        _card("Day P/L", _money(c["day_pl"]), "since 00:00 UTC", _tone(c["day_pl"])),
        _card("Risk : Reward", f"1 : {c['rr_headline']}" if c["rr_headline"] else "—",
              "open position"),
        _card("Balance", f"{balance:.2f}" if balance is not None else "—"),
        _card("Equity", f"{equity:.2f}" if equity is not None else "—"),
        _card("Open positions", len(positions) if live else
              (latest_risk["open_positions"] if latest_risk else "—")),
    ])

    pos_rows = [[
        p.get("symbol") or "—", p["ticket"], p["side"], p["volume"],
        f'{p["entry"]:.5f}' if p["entry"] else "—",
        f'{p["current"]:.5f}' if p["current"] else "—",
        f'{p["pips"]:+.1f}' if p["pips"] is not None else "—",
        _money(p["profit"]),
        f'{p["sl"]:.5f}' if p["sl"] else "—",
        f'{p["tp"]:.5f}' if p["tp"] else "—",
        f'1:{p["rr"]}' if p["rr"] else "—",
    ] for p in positions]

    refresh_on = bool(refresh_seconds and refresh_seconds > 0)

    positions_section = ""
    if live is not None:
        positions_section = f"""
<h2>Open positions</h2>
<div class="panel" id="pos_panel">{_table(
    ["Symbol", "Ticket", "Side", "Vol", "Entry", "Current", "Pips", "P/L",
     "SL", "TP", "R:R"],
    pos_rows)}</div>"""

    signals_section = _collapsible(
        "Recent signals",
        _table(["Time", "Symbol", "Signal", "Reason"], c["signal_rows"]),
        len(c["signals"]), "sig_panel", "sig_count")
    orders_section = _collapsible(
        "Recent orders",
        _table(["Time", "Symbol", "Side", "Vol", "Ticket", "Status", "Message"],
               c["order_rows"]),
        len(c["orders"]), "ord_panel", "ord_count")

    if live is not None and refresh_on:
        head_refresh = (f'<noscript><meta http-equiv="refresh" '
                        f'content="{refresh_seconds}"></noscript>')
        foot_text = f'Live — updating every {refresh_seconds}s in place (no reload).'
        live_js = (
            f'<script>var RS={refresh_seconds};var PORT={port};'
            "var BASE=(location.protocol==='file:')?('http://127.0.0.1:'+PORT):'';"
            f'</script><script>{_LIVE_JS}</script>')
    elif refresh_on:
        head_refresh = f'<meta http-equiv="refresh" content="{refresh_seconds}">'
        foot_text = f'Auto-refreshing every {refresh_seconds}s while the bot runs.'
        live_js = ""
    else:
        head_refresh = ""
        foot_text = "Static snapshot — re-run the dashboard command to refresh."
        live_js = ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{head_refresh}
<title>MT5 AI Bridge — Dashboard</title>
<style>{_CSS}</style></head>
<body>
<h1>{live_dot}MT5 AI Bridge — {'Live' if live else 'Snapshot'} Dashboard</h1>
<div class="meta">
  <b id="m_time">{_esc(est_now(now_utc))}</b> &middot; Session:
  <b id="m_session">{_esc(session_label(now_utc))}</b>
  &middot; Symbols: <b id="m_symbol">{_esc(", ".join(symbols))}</b>
  &middot; Pip size: <b>{pip_size:g}</b>
</div>
{_control_bar(control)}

<div class="cards" id="cards">{cards}</div>
{_prop_panel(prop)}
<h2>Per-symbol</h2>
<div class="panel" id="persym_panel">{_table(["Symbol", "Open", "P/L"],
    _per_symbol_rows(positions, symbols))}</div>
{_thinking_panel(thinking, symbol)}
{_engine_breakdown_panel(engines)}
{_signal_breakdown_panel(c["signal_stats"])}
{positions_section}

<h2>Equity</h2>
<div class="panel" id="eq_panel">{_sparkline(c["equity_series"])}</div>

{signals_section}
{orders_section}

<div class="foot" id="foot_state">{foot_text}</div>
{live_js}
</body></html>"""


def write_dashboard_live(journal: Journal, live: dict, path: str,
                         refresh_seconds: int = 5,
                         control: Optional[dict] = None,
                         thinking: Optional[dict] = None,
                         port: int = 8800, prop: Optional[dict] = None,
                         engines: Optional[list] = None) -> str:
    """Refresh the live dashboard HTML shell (bot writes this each loop)."""
    html_text = build_dashboard(journal, live=live, refresh_seconds=refresh_seconds,
                                control=control, thinking=thinking, port=port,
                                prop=prop, engines=engines)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return path


def write_dashboard_data(journal: Journal, live: dict, path: str,
                         refresh_seconds: int = 1,
                         control: Optional[dict] = None,
                         thinking: Optional[dict] = None,
                         prop: Optional[dict] = None,
                         engines: Optional[list] = None) -> str:
    """Write the JSON snapshot the page polls for in-place live updates.

    Written atomically (temp file + os.replace) so the server never serves a
    half-written file."""
    data = build_dashboard_data(journal, live=live, refresh_seconds=refresh_seconds,
                                control=control, thinking=thinking, prop=prop,
                                engines=engines)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    return path


def write_status(path: str, message: str, refresh_seconds: int = 5) -> str:
    """Write a minimal auto-refreshing status page (used when not connected)."""
    refresh = (f'<meta http-equiv="refresh" content="{refresh_seconds}">'
               if refresh_seconds else "")
    html_text = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"{refresh}"
        "<style>body{font-family:-apple-system,Segoe UI,Arial,sans-serif;"
        "background:#0b1020;color:#e7ecf3;padding:24px}"
        ".warn{background:#2a1620;border:1px solid #5b2330;border-radius:10px;"
        "padding:16px;color:#f0a0a0;font-size:14px}</style></head><body>"
        "<h2>MT5 AI Bridge</h2>"
        f"<div class='warn'>{_esc(message)}</div>"
        "<p style='color:#8895a8;font-size:13px'>The bot is running and will keep "
        "retrying. This page updates automatically.</p></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return path


def save_dashboard(db_path: str = "journal.db", out_path: str = "dashboard.html",
                   refresh_seconds: int = 0) -> str:
    journal = Journal(db_path)
    try:
        html_text = build_dashboard(journal, refresh_seconds=refresh_seconds)
    finally:
        journal.close()
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return out_path


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        prog="mt5_ai_bridge.dashboard",
        description="Generate an HTML dashboard from the journal.")
    p.add_argument("--db", default="journal.db")
    p.add_argument("--out", default="dashboard.html")
    p.add_argument("--refresh", type=int, default=0)
    args = p.parse_args(argv)
    out = save_dashboard(args.db, args.out, args.refresh)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
