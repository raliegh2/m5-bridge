"""Idempotently expose the V10 precision portfolio in ``app.py``.

Run from the repository root, review the diff, then start in READ_ONLY mode.
A backup is created before the application file is changed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"


def main() -> None:
    if not APP.exists():
        raise FileNotFoundError(APP)
    backup = APP.with_suffix(APP.suffix + ".v10_precision.bak")
    if not backup.exists():
        shutil.copy2(APP, backup)

    text = APP.read_text(encoding="utf-8")
    import_line = (
        "from .gbpusd_portfolio_v10 import run_portfolio_v10_cycle\n"
    )
    import_anchor = (
        "from .risk_engine import DailyLossTracker, RiskLimits, check_risk\n"
    )
    if import_line not in text:
        if import_anchor not in text:
            raise RuntimeError("Cannot find risk-engine import anchor")
        text = text.replace(import_anchor, import_anchor + import_line, 1)

    route = '''    if settings.strategy == "gbpusd_v10_precision":
        thinking = run_portfolio_v10_cycle(
            client, journal, settings, account, risk_ok=risk.ok, active=active
        )
        control = {"active": active} if state is not None else None
        _refresh_dashboard(
            client, journal, settings, control=control, thinking=thinking
        )
        _print_status(client, settings, active=active)
        return

'''
    route_anchor = "    # Fast ENTRY read (TIMEFRAME = M15).\n"
    if route not in text:
        if route_anchor not in text:
            raise RuntimeError("Cannot find run-once route anchor")
        text = text.replace(route_anchor, route + route_anchor, 1)

    APP.write_text(text, encoding="utf-8")
    print(
        "V10 precision route installed. Set STRATEGY=gbpusd_v10_precision "
        "and keep MODE=READ_ONLY for validation."
    )


if __name__ == "__main__":
    main()
