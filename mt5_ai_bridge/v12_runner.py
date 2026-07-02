"""Local READ_ONLY/demo runner for the V12 swing-quality candidate."""
from __future__ import annotations

import time
import webbrowser

from . import app
from .control import ControlState, start_control_server
from .mt5_client import create_client
from .risk_engine import DailyLossTracker, RiskLimits, check_risk
from .v12_multisymbol_swing import run_v12_multisymbol_cycle


def run() -> None:
    settings = app.load_settings()
    app.setup_logging(settings.log_level)
    journal = app.Journal(settings.db_path)
    limits = RiskLimits(
        settings.daily_max_loss, settings.total_max_loss,
        settings.max_open_positions,
    )
    tracker = DailyLossTracker()
    control = ControlState(active=True)
    client = create_client()

    if settings.serve_dashboard:
        start_control_server(
            control, settings.dashboard_port, settings.dashboard_path,
            host=settings.dashboard_host, data_path=app._data_path(settings),
        )
        url = f"http://127.0.0.1:{settings.dashboard_port}"
        print(f"Live dashboard: {url}")
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        app.connect(client, settings)
        while True:
            account = client.account_info()
            if account is None:
                raise RuntimeError("account_info() returned None")
            positions = client.positions_get() or []
            day_loss = tracker.update(account.equity)
            risk = check_risk(account, positions, limits, daily_loss=day_loss)
            journal.log_risk_event(
                risk.ok, risk.message, account.balance,
                account.equity, len(positions),
            )
            thinking = run_v12_multisymbol_cycle(
                client, journal, settings, account,
                risk_ok=risk.ok, active=control.is_active(),
            )
            app._refresh_dashboard(
                client, journal, settings,
                control={"active": control.is_active()}, thinking=thinking,
            )
            app._print_status(client, settings, active=control.is_active())
            time.sleep(settings.loop_interval_seconds)
    finally:
        journal.close()
        client.shutdown()


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
