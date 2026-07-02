"""Integrate STRATEGY=gbpusd_v4_satellite_v2 and dashboard engine labels."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
if not (ROOT / "mt5_ai_bridge").exists():
    ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"
DASHBOARD = ROOT / "mt5_ai_bridge" / "dashboard.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise RuntimeError(f"Patch target not found: {label}")
    print(f"Patched: {label}")
    return text.replace(old, new, 1)


def patch_app() -> None:
    text = APP.read_text(encoding="utf-8")
    portfolio_import = "from .gbpusd_portfolio_v2 import run_portfolio_v2_cycle\n"
    if portfolio_import not in text:
        anchor = "from .trade_manager import close_position, modify_position_sl, trailing_sl\n"
        text = replace_once(
            text,
            anchor,
            anchor + portfolio_import,
            "Satellite V2 portfolio import",
        )

    route = '''    if settings.strategy == "gbpusd_v4_satellite_v2":
        thinking = run_portfolio_v2_cycle(
            client, journal, settings, account, risk_ok=risk.ok, active=active
        )
        control = {"active": active} if state is not None else None
        _refresh_dashboard(
            client, journal, settings, control=control, thinking=thinking
        )
        _print_status(client, settings, active=active)
        return

'''
    if route not in text:
        anchor = '    log.info("Risk: %s | day_loss=%.2f | active=%s", risk.message, day_loss, active)\n\n'
        if anchor not in text:
            raise RuntimeError("Patch target not found: Satellite V2 execution route")
        text = text.replace(anchor, anchor + route, 1)
        print("Patched: Satellite V2 execution route")

    metadata_old = '''                "price_current": getattr(p, "price_current", None),
                "sl": p.sl, "tp": p.tp,
'''
    metadata_new = '''                "price_current": getattr(p, "price_current", None),
                "sl": p.sl, "tp": p.tp,
                "magic": getattr(p, "magic", None),
                "comment": getattr(p, "comment", ""),
'''
    if '"magic": getattr(p, "magic", None)' not in text:
        text = replace_once(
            text, metadata_old, metadata_new,
            "dashboard position magic/comment metadata",
        )
    APP.write_text(text, encoding="utf-8")


def patch_dashboard() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    if 'engine = "Satellite V2"' not in text:
        old = '''    return {
        "ticket": pos.get("ticket"), "side": side, "volume": pos.get("volume"),
'''
        new = '''    magic = pos.get("magic")
    comment = str(pos.get("comment") or "")
    upper_comment = comment.upper()
    if magic == 260704 or "V4" in upper_comment:
        engine = "V4 Swing"
    elif magic == 260731 or "SATELLITE V2" in upper_comment:
        engine = "Satellite V2"
    elif magic == 260730 or "SATELLITE" in upper_comment:
        engine = "Satellite Intraday"
    else:
        engine = comment or "Other"

    return {
        "ticket": pos.get("ticket"), "engine": engine,
        "side": side, "volume": pos.get("volume"),
'''
        text = replace_once(text, old, new, "position engine classification")

    text = replace_once(
        text,
        "var rows=ps.map(function(p){return [p.ticket,p.side,p.volume,\n",
        "var rows=ps.map(function(p){return [p.ticket,p.engine,p.side,p.volume,\n",
        "live engine value",
    )
    text = replace_once(
        text,
        "return rowsTable(['Ticket','Side','Vol','Entry','Current','Pips','P/L','SL','TP','R:R'],rows);}",
        "return rowsTable(['Ticket','Engine','Side','Vol','Entry','Current','Pips','P/L','SL','TP','R:R'],rows);}",
        "live engine header",
    )
    text = replace_once(
        text,
        '        p["ticket"], p["side"], p["volume"],\n',
        '        p["ticket"], p["engine"], p["side"], p["volume"],\n',
        "static engine value",
    )
    text = replace_once(
        text,
        '    ["Ticket", "Side", "Vol", "Entry", "Current", "Pips", "P/L", "SL", "TP", "R:R"],\n',
        '    ["Ticket", "Engine", "Side", "Vol", "Entry", "Current", "Pips", "P/L", "SL", "TP", "R:R"],\n',
        "static engine header",
    )
    DASHBOARD.write_text(text, encoding="utf-8")


def main() -> None:
    patch_app()
    patch_dashboard()
    print("GBPUSD V4 + Satellite V2 integration applied.")


if __name__ == "__main__":
    main()
