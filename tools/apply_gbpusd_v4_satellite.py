"""Integrate the V4 swing + H1/M30 satellite portfolio and dashboard labels.

The patch is idempotent and supports repositories where apply_gbpusd_v4.py was
already run as well as clean source checkouts.
"""
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
    portfolio_import = "from .gbpusd_portfolio import run_portfolio_cycle\n"
    if portfolio_import not in text:
        if "from .gbpusd_v4 import run_v4_cycle\n" in text:
            text = text.replace(
                "from .gbpusd_v4 import run_v4_cycle\n",
                "from .gbpusd_v4 import run_v4_cycle\n" + portfolio_import,
                1,
            )
        else:
            text = replace_once(
                text,
                "from .trade_manager import close_position, modify_position_sl, trailing_sl\n",
                "from .trade_manager import close_position, modify_position_sl, trailing_sl\n"
                + portfolio_import,
                "portfolio import",
            )

    portfolio_branch = '''    if settings.strategy == "gbpusd_v4_satellite":
        thinking = run_portfolio_cycle(
            client, journal, settings, account, risk_ok=risk.ok, active=active
        )
        control = {"active": active} if state is not None else None
        _refresh_dashboard(
            client, journal, settings, control=control, thinking=thinking
        )
        _print_status(client, settings, active=active)
        return

'''
    if portfolio_branch not in text:
        anchor = '    log.info("Risk: %s | day_loss=%.2f | active=%s", risk.message, day_loss, active)\n\n'
        if anchor not in text:
            raise RuntimeError("Patch target not found: portfolio execution path")
        text = text.replace(anchor, anchor + portfolio_branch, 1)
        print("Patched: portfolio execution path")

    text = replace_once(
        text,
        '''                "price_current": getattr(p, "price_current", None),
                "sl": p.sl, "tp": p.tp,
''',
        '''                "price_current": getattr(p, "price_current", None),
                "sl": p.sl, "tp": p.tp,
                "magic": getattr(p, "magic", None),
                "comment": getattr(p, "comment", ""),
''',
        "dashboard position engine metadata",
    )
    APP.write_text(text, encoding="utf-8")


def patch_dashboard() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''    return {
        "ticket": pos.get("ticket"), "side": side, "volume": pos.get("volume"),
''',
        '''    magic = pos.get("magic")
    comment = str(pos.get("comment") or "")
    if magic == 260704 or "V4" in comment.upper():
        engine = "V4 Swing"
    elif magic == 260730 or "SATELLITE" in comment.upper():
        engine = "Satellite Intraday"
    else:
        engine = comment or "Other"

    return {
        "ticket": pos.get("ticket"), "engine": engine,
        "side": side, "volume": pos.get("volume"),
''',
        "dashboard position engine classification",
    )
    text = replace_once(
        text,
        '''var rows=ps.map(function(p){return [p.ticket,p.side,p.volume,
''',
        '''var rows=ps.map(function(p){return [p.ticket,p.engine,p.side,p.volume,
''',
        "live position engine value",
    )
    text = replace_once(
        text,
        '''return rowsTable(['Ticket','Side','Vol','Entry','Current','Pips','P/L','SL','TP','R:R'],rows);}''',
        '''return rowsTable(['Ticket','Engine','Side','Vol','Entry','Current','Pips','P/L','SL','TP','R:R'],rows);}''',
        "live position engine header",
    )
    text = replace_once(
        text,
        '''        p["ticket"], p["side"], p["volume"],
''',
        '''        p["ticket"], p["engine"], p["side"], p["volume"],
''',
        "static position engine value",
    )
    text = replace_once(
        text,
        '''    ["Ticket", "Side", "Vol", "Entry", "Current", "Pips", "P/L", "SL", "TP", "R:R"],
''',
        '''    ["Ticket", "Engine", "Side", "Vol", "Entry", "Current", "Pips", "P/L", "SL", "TP", "R:R"],
''',
        "static position engine header",
    )
    DASHBOARD.write_text(text, encoding="utf-8")


def main() -> None:
    patch_app()
    patch_dashboard()
    print("GBPUSD V4 + Satellite portfolio integration applied.")


if __name__ == "__main__":
    main()
