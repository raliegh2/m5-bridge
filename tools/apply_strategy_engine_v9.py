"""Idempotently expose STRATEGY=gbpusd_v4_satellite_v3 in the live app.

Run from the repository root after the V2 integration is present. Backups are
created before app.py and dashboard.py are changed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"
DASHBOARD = ROOT / "mt5_ai_bridge" / "dashboard.py"


def _backup(path: Path) -> None:
    backup = path.with_suffix(path.suffix + ".v9.bak")
    if not backup.exists():
        shutil.copy2(path, backup)


def _insert_once(text: str, anchor: str, addition: str, label: str) -> str:
    if addition in text:
        return text
    if anchor not in text:
        raise RuntimeError(f"Cannot apply V9 patch; missing anchor: {label}")
    return text.replace(anchor, anchor + addition, 1)


def patch_app() -> None:
    if not APP.exists():
        raise FileNotFoundError(APP)
    _backup(APP)
    text = APP.read_text(encoding="utf-8")
    import_line = "from .gbpusd_portfolio_v3 import run_portfolio_v3_cycle\n"
    import_anchor = "from .gbpusd_portfolio_v2 import run_portfolio_v2_cycle\n"
    text = _insert_once(text, import_anchor, import_line, "portfolio V2 import")

    v2_route = '''    if settings.strategy == "gbpusd_v4_satellite_v2":
        thinking = run_portfolio_v2_cycle(
            client, journal, settings, account, risk_ok=risk.ok, active=active
        )
'''
    v3_route = '''    if settings.strategy == "gbpusd_v4_satellite_v3":
        thinking = run_portfolio_v3_cycle(
            client, journal, settings, account, risk_ok=risk.ok, active=active
        )
        control = {"active": active} if state is not None else None
        _refresh_dashboard(
            client, journal, settings, control=control, thinking=thinking
        )
        _print_status(client, settings, active=active)
        return

'''
    if v3_route not in text:
        if v2_route not in text:
            raise RuntimeError("Cannot apply V9 patch; V2 execution route is absent")
        text = text.replace(v2_route, v3_route + v2_route, 1)
    APP.write_text(text, encoding="utf-8")


def patch_dashboard() -> None:
    if not DASHBOARD.exists():
        return
    _backup(DASHBOARD)
    text = DASHBOARD.read_text(encoding="utf-8")
    old = '''    elif magic == 260731 or "SATELLITE V2" in upper_comment:
        engine = "Satellite V2"
'''
    new = '''    elif magic == 260732 or "SATELLITE V3" in upper_comment:
        engine = "Satellite V3 / V9"
    elif magic == 260731 or "SATELLITE V2" in upper_comment:
        engine = "Satellite V2"
'''
    if new not in text:
        if old not in text:
            raise RuntimeError("Cannot apply V9 dashboard label; V2 label is absent")
        text = text.replace(old, new, 1)
    DASHBOARD.write_text(text, encoding="utf-8")


def main() -> None:
    patch_app()
    patch_dashboard()
    print("V9 candidate route installed. Set STRATEGY=gbpusd_v4_satellite_v3.")


if __name__ == "__main__":
    main()
