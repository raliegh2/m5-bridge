"""Clean Windows entrypoint for the V14.3 satellite MT5 bot.

The terminal intentionally keeps one continuously updated status line instead of
printing one line per market loop. Account state is refreshed once per second.
The expensive four-symbol strategy calculation runs only when a new completed H1
bar is detected, which matches the completed-candle H1/H4/D1 strategy design.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
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
from mt5_ai_bridge.v14_3_live_signals import resolve_all_symbols
from v14_3_satellite_live_runner import (
    DASHBOARD_SNAPSHOT,
    _position_snapshot,
    scan_once,
)

# Fast account/position heartbeat. The trading engines themselves remain based on
# completed H1/H4/D1 candles and are recalculated only when a new H1 close appears.
HEARTBEAT_SECONDS = 1.0
LOOKBACK_HOURS = 8
DASHBOARD_HOST = os.getenv("V14_3_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("V14_3_DASHBOARD_PORT", "8800"))
SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD")
_last_status_width = 0


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
    """Clear only the current physical console row without emitting a newline."""
    global _last_status_width
    columns = max(20, shutil.get_terminal_size(fallback=(120, 24)).columns - 1)
    width = min(_last_status_width, columns)
    sys.stdout.write("\r" + (" " * width) + "\r")
    sys.stdout.flush()
    _last_status_width = 0


def _status_line(
    diagnostics: dict[str, Any],
    trades_placed: int = 0,
) -> str:
    account = diagnostics.get("account") or {}
    positions = diagnostics.get("positions") or []
    strategy_state = diagnostics.get("strategy_state", "WAITING")
    schedule = diagnostics.get("scan_schedule") or {}

    def scan_clock(group: str) -> str:
        value = (schedule.get(group) or {}).get("last_scan_at")
        if not value:
            return "--:--"
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime(
                "%H:%M"
            )
        except ValueError:
            return "--:--"

    return (
        f"{_local_time()} | {diagnostics.get('execution_mode', 'UNKNOWN')} | "
        f"ACTIVE | trades {int(trades_placed)} | open {len(positions)} | "
        f"P/L {_signed_money(account.get('floating_profit'))} | "
        f"equity {_money(account.get('equity'))} | "
        f"balance {_money(account.get('balance'))} | "
        f"engine {strategy_state} | scans UTC: "
        f"GBP-M1 {scan_clock('GBP_ICT')} / "
        f"FX-H1/H4/D1 {scan_clock('FX_PORTFOLIO')} / "
        f"Gold-M30/H4 {scan_clock('GOLD')}"
    )


def _print_status(
    diagnostics: dict[str, Any],
    trades_placed: int,
) -> None:
    """Rewrite one bounded physical row even after thousands of heartbeats."""
    global _last_status_width
    columns = max(20, shutil.get_terminal_size(fallback=(120, 24)).columns - 1)
    rendered = _status_line(
        diagnostics, trades_placed=trades_placed
    )[:columns]
    write_width = min(max(_last_status_width, len(rendered)), columns)
    sys.stdout.write("\r" + rendered.ljust(write_width))
    sys.stdout.flush()
    _last_status_width = len(rendered)


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
    print(f" Mode            : {config.execution_mode}")
    print(f" Symbols         : {', '.join(SYMBOLS)}")
    print(f" Account refresh : every {HEARTBEAT_SECONDS:.0f} second")
    print(" Strategy scan   : on each new completed H1 candle")
    print(f" Dashboard       : {dashboard_url}")
    print(" Terminal        : one continuously updated live status line")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


def _closed_h1_signature(
    client: Any,
    broker_map: dict[str, str],
) -> tuple[tuple[str, int | None], ...]:
    """Return the latest completed H1 timestamp for every configured symbol."""
    values: list[tuple[str, int | None]] = []
    for symbol in SYMBOLS:
        rates = client.copy_rates_from_pos(broker_map[symbol], "H1", 1, 1)
        marker: int | None = None
        if rates is not None and len(rates):
            row = rates[0]
            try:
                marker = int(row["time"])
            except (KeyError, TypeError, ValueError, IndexError):
                marker = int(getattr(row, "time", 0) or 0) or None
        values.append((symbol, marker))
    return tuple(values)


def _account_payload(client: Any, executor: SatelliteLiveExecutor) -> dict[str, Any]:
    account = client.account_info()
    return {
        "login": getattr(account, "login", None),
        "server": str(getattr(account, "server", "")),
        "balance": float(getattr(account, "balance", 0.0) or 0.0),
        "equity": float(getattr(account, "equity", 0.0) or 0.0),
        "floating_profit": float(getattr(account, "profit", 0.0) or 0.0),
        "margin": float(getattr(account, "margin", 0.0) or 0.0),
        "free_margin": float(getattr(account, "margin_free", 0.0) or 0.0),
        "trade_mode": getattr(account, "trade_mode", None),
        "demo_confirmed": (
            executor._is_demo(account) if account is not None else False
        ),
    }


def _heartbeat_snapshot(
    client: Any,
    executor: SatelliteLiveExecutor,
    prior: dict[str, Any],
    trades_placed: int,
) -> dict[str, Any]:
    """Refresh account and position state without rebuilding all strategies."""
    updated = dict(prior)
    updated.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner_status": "RUNNING",
        "execution_mode": executor.config.execution_mode,
        "account": _account_payload(client, executor),
        "positions": _position_snapshot(client, executor),
        "trades_placed_since_start": trades_placed,
        "next_scan_seconds": HEARTBEAT_SECONDS,
    })
    return updated


def main() -> None:
    # Enable ANSI handling for the current Windows console session.
    if os.name == "nt":
        os.system("")

    config = LiveRunnerConfig.from_env()
    settings = load_settings()
    client = create_client()
    dashboard = LiveDashboard(
        snapshot_path=Path(DASHBOARD_SNAPSHOT),
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
    )
    recent_decisions: list[dict[str, Any]] = []
    trades_placed = 0

    connect(client, settings)
    executor = SatelliteLiveExecutor(client, config)
    broker_map = resolve_all_symbols(client)

    diagnostics: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner_status": "STARTING",
        "execution_mode": config.execution_mode,
        "account": {},
        "positions": [],
        "engines": [],
        "decisions": [],
        "generation": {"broker_symbols": broker_map},
        "candidate_count": 0,
        "orders_filled": 0,
        "trades_placed_since_start": 0,
        "scan_latency_ms": 0,
        "strategy_state": "STARTING",
        "next_scan_seconds": HEARTBEAT_SECONDS,
    }
    dashboard.write(diagnostics)
    dashboard.start(open_browser=False)
    _startup_banner(config, dashboard.url)
    time.sleep(0.25)
    _open_dashboard(dashboard.url)

    last_h1_signature: tuple[tuple[str, int | None], ...] | None = None

    try:
        next_heartbeat = time.monotonic()
        while True:
            try:
                signature = _closed_h1_signature(client, broker_map)
                new_closed_bar = (
                    last_h1_signature is None or signature != last_h1_signature
                )

                if new_closed_bar:
                    diagnostics["strategy_state"] = "SCANNING"
                    dashboard.write(diagnostics)
                    _print_status(diagnostics, trades_placed=trades_placed)

                    # Keep detailed lower-level JSON out of the normal terminal.
                    if config.execution_mode == "APPROVAL":
                        diagnostics = scan_once(
                            client,
                            executor,
                            lookback_hours=LOOKBACK_HOURS,
                            recent_decisions=recent_decisions,
                        )
                    else:
                        with (
                            contextlib.redirect_stdout(io.StringIO()),
                            contextlib.redirect_stderr(io.StringIO()),
                        ):
                            diagnostics = scan_once(
                                client,
                                executor,
                                lookback_hours=LOOKBACK_HOURS,
                                recent_decisions=recent_decisions,
                            )

                    trades_placed += int(diagnostics.get("orders_filled", 0) or 0)
                    last_h1_signature = signature
                    diagnostics["last_strategy_scan_at"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                    diagnostics["strategy_state"] = "WAITING"

                diagnostics = _heartbeat_snapshot(
                    client,
                    executor,
                    diagnostics,
                    trades_placed,
                )
                dashboard.write(diagnostics)
                _print_status(diagnostics, trades_placed=trades_placed)
            except Exception as exc:  # noqa: BLE001
                _clear_status_line()
                print(f"[{_local_time()}] RUNNER ERROR | {type(exc).__name__}: {exc}")
                diagnostics.update({
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "runner_status": "ERROR",
                    "execution_mode": config.execution_mode,
                    "message": f"{type(exc).__name__}: {exc}",
                    "strategy_state": "ERROR",
                    "trades_placed_since_start": trades_placed,
                })
                dashboard.write(diagnostics)

            next_heartbeat += HEARTBEAT_SECONDS
            delay = next_heartbeat - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # A new-candle strategy calculation can take longer than one second.
                # Resume the one-second heartbeat without accumulating schedule lag.
                next_heartbeat = time.monotonic()
    except KeyboardInterrupt:
        _clear_status_line()
        print("Bot stopped by user.")
    finally:
        dashboard.stop()
        client.shutdown()


if __name__ == "__main__":
    main()
