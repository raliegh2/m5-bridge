"""Idempotently add the V10 multi-symbol route to mt5_ai_bridge/app.py."""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mt5_ai_bridge" / "app.py"


def main() -> None:
    if not APP.exists():
        raise FileNotFoundError(APP)
    backup = APP.with_suffix(APP.suffix + ".v10_multisymbol.bak")
    if not backup.exists():
        shutil.copy2(APP, backup)
    text = APP.read_text(encoding="utf-8")
    import_line = "from .v10_multisymbol import run_v10_multisymbol_cycle\n"
    anchor = "from .risk_engine import DailyLossTracker, RiskLimits, check_risk\n"
    if import_line not in text:
        if anchor not in text:
            raise RuntimeError("Cannot locate app import anchor")
        text = text.replace(anchor, anchor + import_line, 1)

    route = '''    if settings.strategy == "v10_multisymbol":
        thinking = run_v10_multisymbol_cycle(
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
            raise RuntimeError("Cannot locate app route anchor")
        text = text.replace(route_anchor, route + route_anchor, 1)
    APP.write_text(text, encoding="utf-8")
    print("Installed STRATEGY=v10_multisymbol route. Keep MODE=READ_ONLY first.")


if __name__ == "__main__":
    main()
