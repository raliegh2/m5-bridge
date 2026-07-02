"""Standalone local runner for the V11 multi-symbol swing candidate.

Run with:
    python -m mt5_ai_bridge.v11_runner

This avoids modifying app.py. It reuses the established connection, dashboard,
journal and reconnect loop, while replacing one iteration with the V11 cycle.
"""
from __future__ import annotations

from typing import Optional

from . import app
from .control import ControlState
from .risk_engine import check_risk
from .v11_multisymbol_swing import run_v11_multisymbol_cycle


def _run_once_v11(client, journal, settings, strategy_fn, limits, tracker,
                  planner_cfgs, state: Optional[ControlState] = None) -> None:
    active = state.is_active() if state is not None else True
    account = client.account_info()
    if account is None:
        raise RuntimeError("account_info() returned None")
    positions = client.positions_get() or []
    day_loss = tracker.update(account.equity)
    risk = check_risk(account, positions, limits, daily_loss=day_loss)
    journal.log_risk_event(
        risk.ok, risk.message, account.balance, account.equity, len(positions)
    )
    thinking = run_v11_multisymbol_cycle(
        client, journal, settings, account, risk_ok=risk.ok, active=active
    )
    control = {"active": active} if state is not None else None
    app._refresh_dashboard(
        client, journal, settings, control=control, thinking=thinking
    )
    app._print_status(client, settings, active=active)


def run() -> None:
    original = app._run_once
    app._run_once = _run_once_v11
    try:
        app.run()
    finally:
        app._run_once = original


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
