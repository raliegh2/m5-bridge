"""Clean V14.3 runner with split H1 and GBP ICT M1 scheduling.

Account and position state refresh every second. The existing V12 and native
satellite ICT portfolio is recalculated on completed H1 candles, while the
recovered GBPUSD/GBPJPY V14.3 provider is checked on completed M1 candles.
"""
from __future__ import annotations

import contextlib
import io
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.v14_3_live_dashboard import LiveDashboard
from mt5_ai_bridge.v14_3_live_execution import LiveRunnerConfig, SatelliteLiveExecutor
from mt5_ai_bridge.v14_3_live_signals import (
    load_legacy_gbp_ict_signals,
    resolve_all_symbols,
)
from v14_3_satellite_bot import (
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    HEARTBEAT_SECONDS,
    LOOKBACK_HOURS,
    SYMBOLS,
    _clear_status_line,
    _heartbeat_snapshot,
    _open_dashboard,
    _print_status,
)
from v14_3_satellite_live_runner import (
    DASHBOARD_SNAPSHOT,
    DIAGNOSTICS,
    EXECUTION_LOG,
    _append_jsonl,
    _atomic_json,
    _decision_rationale,
    _engine_status,
    _position_snapshot,
    scan_once,
)

GBP_ICT_SYMBOLS = ("GBPUSD", "GBPJPY")


def _closed_bar_signature(
    client: Any,
    broker_map: dict[str, str],
    timeframe: str,
    symbols: Iterable[str],
) -> tuple[tuple[str, int | None], ...]:
    """Return latest completed-bar timestamps; index zero is never requested."""
    values: list[tuple[str, int | None]] = []
    for symbol in symbols:
        rates = client.copy_rates_from_pos(broker_map[symbol], timeframe, 1, 1)
        marker: int | None = None
        if rates is not None and len(rates):
            row = rates[0]
            try:
                marker = int(row["time"])
            except (KeyError, TypeError, ValueError, IndexError):
                marker = int(getattr(row, "time", 0) or 0) or None
        values.append((symbol, marker))
    return tuple(values)


def _merge_gbp_scan(
    prior: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    """Merge the lightweight M1 scan without erasing H1 engine state."""
    merged = dict(prior)
    for key in (
        "generated_at",
        "runner_status",
        "execution_mode",
        "account",
        "positions",
        "decisions",
        "orders_filled",
        "read_only_proposals",
        "candidate_count",
        "result_count",
        "scan_latency_ms",
        "state_path",
    ):
        if key in update:
            merged[key] = update[key]

    generation = dict(prior.get("generation") or {})
    generation.update(update.get("generation") or {})
    merged["generation"] = generation

    prior_engines = {
        str(item.get("engine")): dict(item)
        for item in prior.get("engines") or []
    }
    for item in update.get("engines") or []:
        if str(item.get("engine")) in {"ICT_V14_3_GBPUSD", "ICT_V14_3_GBPJPY"}:
            prior_engines[str(item["engine"])] = dict(item)
    merged["engines"] = list(prior_engines.values()) or list(update.get("engines") or [])

    symbols = dict(prior.get("symbols") or {})
    for symbol in GBP_ICT_SYMBOLS:
        if symbol in (update.get("symbols") or {}):
            symbols[symbol] = update["symbols"][symbol]
    merged["symbols"] = symbols
    return merged


def scan_gbp_ict_once(
    client: Any,
    executor: SatelliteLiveExecutor,
    broker_map: dict[str, str],
    recent_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run only the recovered GBP ICT provider on a completed M1 boundary."""
    started = time.perf_counter()
    now = datetime.now(timezone.utc)
    signals, provider_status = load_legacy_gbp_ict_signals(client, broker_map)
    generation = {
        "broker_symbols": broker_map,
        "scan_scope": "GBP_ICT_M1",
        "legacy_gbp_ict_provider": provider_status,
        "legacy_gbp_ict_candidates": len(signals),
    }
    results: list[dict[str, Any]] = []

    for signal in signals:
        result = executor.place(signal, now=now)
        payload = {
            "created_at": now.isoformat(),
            "scan_scope": "GBP_ICT_M1",
            "signal_key": signal.key,
            "signal": asdict(signal),
            "result": asdict(result),
        }
        results.append(payload)
        _append_jsonl(EXECUTION_LOG, payload)
        recent_decisions.insert(0, {
            "time": now.isoformat(),
            "symbol": signal.symbol,
            "engine": signal.engine,
            "setup": signal.setup,
            "mode": signal.mode,
            "side": signal.side,
            "risk_percent": round(
                float(result.risk_percent or signal.requested_risk_percent), 4
            ),
            "ok": bool(result.ok),
            "code": result.code,
            "rationale": _decision_rationale(signal, result),
            "metadata": dict(signal.metadata or {}),
        })
    del recent_decisions[100:]

    account = client.account_info()
    positions = _position_snapshot(client, executor)
    per_symbol: dict[str, Any] = {}
    for symbol in GBP_ICT_SYMBOLS:
        symbol_signals = [item for item in signals if item.symbol == symbol]
        symbol_results = [
            item for item in results if item["signal"]["symbol"] == symbol
        ]
        per_symbol[symbol] = {
            "v12_candidates": 0,
            "ict_candidates": len(symbol_signals),
            "open_positions": sum(
                symbol in str(item["symbol"]).upper() for item in positions
            ),
            "results": [
                {
                    "engine": item["signal"]["engine"],
                    "code": item["result"]["code"],
                    "message": item["result"]["message"],
                }
                for item in symbol_results
            ],
        }

    diagnostics = {
        "generated_at": now.isoformat(),
        "runner_status": "RUNNING",
        "execution_mode": executor.config.execution_mode,
        "account": {
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
        },
        "generation": generation,
        "candidate_count": len(signals),
        "result_count": len(results),
        "orders_filled": sum(
            item["result"]["code"] == "ORDER_FILLED" for item in results
        ),
        "read_only_proposals": sum(
            item["result"]["code"] == "READ_ONLY_PROPOSAL" for item in results
        ),
        "positions": positions,
        "engines": _engine_status(signals, results, generation),
        "decisions": list(recent_decisions),
        "symbols": per_symbol,
        "state_path": executor.config.state_path,
        "scan_latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "next_scan_seconds": HEARTBEAT_SECONDS,
    }
    _atomic_json(DIAGNOSTICS, diagnostics)
    return diagnostics


def _startup_banner(config: LiveRunnerConfig, dashboard_url: str) -> None:
    print("=" * 76)
    print(" V14.3 SATELLITE TRADING BOT — LIVE GBP ICT PROVIDER")
    print("=" * 76)
    print(f" Mode            : {config.execution_mode}")
    print(f" Symbols         : {', '.join(SYMBOLS)}")
    print(f" Account refresh : every {HEARTBEAT_SECONDS:.0f} second")
    print(" H1 strategies   : on each new completed H1 candle")
    print(" GBP ICT scan    : on each new completed M1 candle")
    print(f" Dashboard       : {dashboard_url}")
    print(" Terminal        : one continuously updated live status line")
    print(" Press Ctrl+C to stop the bot.")
    print("-" * 76)


def main() -> None:
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
    last_m1_signature: tuple[tuple[str, int | None], ...] | None = None

    try:
        next_heartbeat = time.monotonic()
        while True:
            try:
                h1_signature = _closed_bar_signature(
                    client, broker_map, "H1", SYMBOLS
                )
                m1_signature = _closed_bar_signature(
                    client, broker_map, "M1", GBP_ICT_SYMBOLS
                )
                new_h1 = (
                    last_h1_signature is None
                    or h1_signature != last_h1_signature
                )
                new_m1 = (
                    last_m1_signature is None
                    or m1_signature != last_m1_signature
                )

                if new_h1:
                    diagnostics["strategy_state"] = "SCANNING H1"
                    dashboard.write(diagnostics)
                    _print_status(diagnostics, trades_placed=trades_placed)
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
                    last_h1_signature = h1_signature
                    # The full scan includes the GBP provider, so avoid rescanning the
                    # same completed M1 bar immediately afterward.
                    last_m1_signature = m1_signature
                    diagnostics["last_h1_strategy_scan_at"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                    diagnostics["strategy_state"] = "WAITING"
                elif new_m1:
                    diagnostics["strategy_state"] = "SCANNING M1 ICT"
                    dashboard.write(diagnostics)
                    _print_status(diagnostics, trades_placed=trades_placed)
                    if config.execution_mode == "APPROVAL":
                        update = scan_gbp_ict_once(
                            client, executor, broker_map, recent_decisions
                        )
                    else:
                        with (
                            contextlib.redirect_stdout(io.StringIO()),
                            contextlib.redirect_stderr(io.StringIO()),
                        ):
                            update = scan_gbp_ict_once(
                                client, executor, broker_map, recent_decisions
                            )
                    trades_placed += int(update.get("orders_filled", 0) or 0)
                    diagnostics = _merge_gbp_scan(diagnostics, update)
                    last_m1_signature = m1_signature
                    diagnostics["last_gbp_ict_scan_at"] = datetime.now(
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
                print(
                    f"[{datetime.now().astimezone().strftime('%I:%M:%S %p')}] "
                    f"RUNNER ERROR | {type(exc).__name__}: {exc}"
                )
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
                next_heartbeat = time.monotonic()
    except KeyboardInterrupt:
        _clear_status_line()
        print("Bot stopped by user.")
    finally:
        dashboard.stop()
        client.shutdown()


if __name__ == "__main__":
    main()
