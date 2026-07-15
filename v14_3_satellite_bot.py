"""Clean Windows entrypoint for the V14.3 satellite MT5 bot.

This preserves the earlier bot's terminal structure: one compact updating status
line, concise trade/signal announcements, an automatically launched dashboard,
and no full JSON dump on every market scan.

The one-second target interval is configured in this file. The strategies still
use completed H1/H4/D1 candles; the fast loop refreshes account state, dashboard
state, risk controls and completed-candle signal detection.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.v14_3_live_dashboard import LiveDashboard
from mt5_ai_bridge.v14_3_live_execution import LiveRunnerConfig, SatelliteLiveExecutor
from v14_3_satellite_live_runner import DASHBOARD_SNAPSHOT, scan_once

# Normal startup needs no interval argument. Change this value only when the
# required polling cadence changes. The loop never runs faster than one second.
SCAN_INTERVAL_SECONDS = 1.0
LOOKBACK_HOURS = 8
DASHBOARD_HOST = os.getenv("V14_3_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("V14_3_DASHBOARD_PORT", "8800"))
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
STATUS_WIDTH = 190


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _signed_money(value: Any) -> str:
    try:
        return f"{float(value):+,.2f}"
    except (TypeError, ValueError):
        return "+0.00"


def _local_time() -> str:
    return datetime.now().astimezone().strftime("%I:%M:%S %p")


def _clear_status_line() -> None:
    sys.stdout.write("\r" + (" " * STATUS_WIDTH) + "\r")
    sys.stdout.flush()


def _status_line(diagnostics: dict[str, Any]) -> str:
    account = diagnostics.get("account") or {}
    positions = diagnostics.get("positions") or []
    return (
        f"{_local_time()} | {diagnostics.get('execution_mode', 'UNKNOWN')} | "
        f"ACTIVE | open {len(positions)} | "
        f"balance {_money(account.get('balance'))} | "
        f"equity {_money(account.get('equity'))} | "
        f"P/L {_signed_money(account.get('floating_profit'))} | "
        f"signals {int(diagnostics.get('candidate_count', 0) or 0)} | "
        f"scan {float(diagnostics.get('scan_latency_ms', 0.0) or 0.0):,.0f} ms"
    )


def _print_status(diagnostics: dict[str, Any]) -> None:
    line = _status_line(diagnostics)
    sys.stdout.write("\r" + line[:STATUS_WIDTH].ljust(STATUS_WIDTH))
    sys.stdout.flush()


def _decision_key(decision: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(decision.get("time", "")),
        str(decision.get("symbol", "")),
        str(decision.get("engine", "")),
        str(decision.get("setup", "")),
    )


def _announce_new_decisions(
    decisions: list[dict[str, Any]],
    announced: set[tuple[str, str, str, str]],
) -> None:
    new_items = [item for item in reversed(decisions) if _decision_key(item) not in announced]
    for item in new_items:
        announced.add(_decision_key(item))
        _clear_status_line()
        risk = item.get("risk_percent", 0)
        print(
            f"[{_local_time()}] {item.get('symbol', 'UNKNOWN')} | "
            f"{item.get('engine', 'UNKNOWN_ENGINE')} | {item.get('side', 'WAIT')} | "
            f"{item.get('code', 'UNKNOWN')} | risk {risk}%"
        )


def _open_dashboard(url: str) -> None:
    """Open the dashboard using the normal browser, with a Windows fallback."""
    try:
        opened = bool(webbrowser.open_new_tab(url))
    except Exception:  # noqa: BLE001
        opened = False
    if not opened and hasattr(os, "startfile"):
        try:
            os.startfile(url)  # type: ignore[attr-defined]
        except OSError:
            pass


def _startup_banner(config: LiveRunnerConfig, dashboard_url: str) -> None:
    print("=" * 76)
    print(" V14.3 SATELLITE TRADING BOT")
    print("=" * 76)
    print(f" Mode       : {config.execution_mode}")
    print(f" Symbols    : {', '.join(SYMBOLS)}")
    print(f" Scan target: {SCAN_INTERVAL_SECONDS:.0f} second")
    print(f" Dashboard  : {dashboard_url}")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


def main() -> None:
    config = LiveRunnerConfig.from_env()
    settings = load_settings()
    client = create_client()
    dashboard = LiveDashboard(
        snapshot_path=Path(DASHBOARD_SNAPSHOT),
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
    )
    recent_decisions: list[dict[str, Any]] = []
    announced: set[tuple[str, str, str, str]] = set()

    connect(client, settings)
    executor = SatelliteLiveExecutor(client, config)

    initial = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner_status": "STARTING",
        "execution_mode": config.execution_mode,
        "account": {},
        "positions": [],
        "engines": [],
        "decisions": [],
        "generation": {},
        "candidate_count": 0,
        "scan_latency_ms": 0,
        "next_scan_seconds": SCAN_INTERVAL_SECONDS,
    }
    dashboard.write(initial)
    dashboard.start(open_browser=False)
    _startup_banner(config, dashboard.url)
    time.sleep(0.25)
    _open_dashboard(dashboard.url)

    try:
        next_scan = time.monotonic()
        while True:
            try:
                # The lower-level runner retains detailed JSON output for debugging.
                # This normal bot entrypoint suppresses that output and restores the
                # compact terminal presentation used by earlier bot versions.
                if config.execution_mode == "APPROVAL":
                    diagnostics = scan_once(
                        client,
                        executor,
                        lookback_hours=LOOKBACK_HOURS,
                        recent_decisions=recent_decisions,
                    )
                else:
                    with contextlib.redirect_stdout(io.StringIO()):
                        diagnostics = scan_once(
                            client,
                            executor,
                            lookback_hours=LOOKBACK_HOURS,
                            recent_decisions=recent_decisions,
                        )

                diagnostics["next_scan_seconds"] = SCAN_INTERVAL_SECONDS
                dashboard.write(diagnostics)
                _announce_new_decisions(recent_decisions, announced)
                _print_status(diagnostics)
            except Exception as exc:  # noqa: BLE001
                _clear_status_line()
                print(f"[{_local_time()}] RUNNER ERROR | {type(exc).__name__}: {exc}")
                dashboard.write({
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "runner_status": "ERROR",
                    "execution_mode": config.execution_mode,
                    "message": f"{type(exc).__name__}: {exc}",
                    "account": {},
                    "positions": [],
                    "engines": [],
                    "decisions": recent_decisions,
                    "generation": {},
                    "candidate_count": 0,
                    "scan_latency_ms": 0,
                    "next_scan_seconds": SCAN_INTERVAL_SECONDS,
                })

            next_scan += SCAN_INTERVAL_SECONDS
            delay = next_scan - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # Full five-symbol completed-candle calculations may take longer
                # than one second. Do not accumulate an ever-growing timing backlog.
                next_scan = time.monotonic()
    except KeyboardInterrupt:
        _clear_status_line()
        print("Bot stopped by user.")
    finally:
        dashboard.stop()
        client.shutdown()


if __name__ == "__main__":
    main()
