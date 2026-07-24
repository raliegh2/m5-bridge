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
from mt5_ai_bridge.v14_21_order_flow import OrderFlowMonitor
from mt5_ai_bridge.v14_21_scan_audit import ScanAuditJournal
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
    apply_engine_runtime_metadata,
    scan_once,
)

GBP_ICT_SYMBOLS = ("GBPUSD", "GBPJPY")


def _signature_details(
    signature: tuple[tuple[str, int | None], ...],
) -> dict[str, int | None]:
    return {symbol: marker for symbol, marker in signature}


def _restore_persistent_scan_time(
    diagnostics: dict[str, Any],
    group: str,
    audit_event: dict[str, Any] | None,
) -> None:
    """Restore last-scan visibility after restart without rescanning a bar."""
    if not audit_event:
        return
    recorded_at = audit_event.get("recorded_at")
    schedule = diagnostics.setdefault("scan_schedule", {}).setdefault(
        group, {}
    )
    if recorded_at and not schedule.get("last_scan_at"):
        schedule["last_scan_at"] = recorded_at


def _record_signature_gaps(
    audit: ScanAuditJournal,
    scope: str,
    signature: tuple[tuple[str, int | None], ...],
    timeframe_seconds: int,
    recovery_bars: int,
) -> None:
    for symbol, marker in signature:
        if marker is None:
            audit.record(
                scope,
                "BAR_TIMESTAMP_UNAVAILABLE",
                details={"symbol": symbol},
            )
            continue
        cursor_scope = f"{scope}:{symbol}"
        previous = audit.cursor(cursor_scope)
        if previous is None or marker <= previous:
            continue
        missing = max(0, (int(marker) - previous) // timeframe_seconds - 1)
        if missing:
            audit.record(
                scope,
                "CATCH_UP_GAP_DETECTED",
                completed_bar_time=int(marker),
                details={
                    "symbol": symbol,
                    "previous_bar_time": previous,
                    "missing_completed_bars": missing,
                    "recovery_bars": recovery_bars,
                    "recovery_window_exceeded": missing > recovery_bars,
                },
            )


def _advance_signature_cursors(
    audit: ScanAuditJournal,
    scope: str,
    signature: tuple[tuple[str, int | None], ...],
) -> None:
    for symbol, marker in signature:
        if marker is not None:
            audit.set_cursor(f"{scope}:{symbol}", int(marker))


def _pending_gold_bars(
    client: Any,
    broker_symbol: str,
    audit: ScanAuditJournal,
    max_bars: int,
) -> list[tuple[int, int]]:
    """Return oldest-first ``(bar_time, shift)`` pairs not yet journaled."""
    count = max(2, int(max_bars))
    rates = client.copy_rates_from_pos(broker_symbol, "M30", 1, count)
    if rates is None or not len(rates):
        return []
    markers: list[int] = []
    for row in rates:
        try:
            marker = int(row["time"])
        except (KeyError, TypeError, ValueError, IndexError):
            marker = int(getattr(row, "time", 0) or 0)
        if marker > 0:
            markers.append(marker)
    markers = sorted(set(markers))
    if not markers:
        return []
    previous = audit.cursor("GOLD_M30")
    if previous is None:
        selected = [markers[-1]]
    else:
        selected = [marker for marker in markers if marker > previous]
        if selected and previous < markers[0]:
            audit.record(
                "GOLD_M30",
                "CATCH_UP_WINDOW_EXCEEDED",
                completed_bar_time=selected[-1],
                details={
                    "previous_bar_time": previous,
                    "oldest_available_bar_time": markers[0],
                    "max_catch_up_bars": count,
                },
            )
    newest_index = len(markers) - 1
    return [
        (marker, newest_index - markers.index(marker))
        for marker in selected
    ]


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
        "engines": _engine_status(
            signals, results, generation, recent_decisions
        ),
        "decisions": list(recent_decisions),
        "symbols": per_symbol,
        "state_path": executor.config.state_path,
        "scan_latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "next_scan_seconds": HEARTBEAT_SECONDS,
    }
    _atomic_json(DIAGNOSTICS, diagnostics)
    return diagnostics


def _scan_gold_once(
    client: Any,
    executor: Any,
    broker_symbol: str,
    risk_percent: float,
    recent_decisions: list[dict[str, Any]] | None = None,
    *,
    completed_m30_shift: int = 0,
    completed_bar_time: int | None = None,
    audit: ScanAuditJournal | None = None,
) -> int:
    """Opt-in gold intraday breakout scan (the metals satellite).

    Builds a GOLD-mode LiveSignal from a completed-M30 breakout and routes it
    through the SAME executor as the FX engines, so gold shares every per-trade,
    combined open-risk, drawdown and demo-account control. Returns 1 on a fill.
    """
    from mt5_ai_bridge.gold_intraday_engine import (
        evaluate_gold_setup_diagnostic, ENGINE, GOLD_SYMBOL)
    from mt5_ai_bridge.v14_3_live_execution import LiveSignal
    evaluation = evaluate_gold_setup_diagnostic(
        client,
        broker_symbol,
        completed_m30_shift=completed_m30_shift,
    )
    setup = evaluation.setup
    if setup is None:
        if audit is not None:
            audit.record(
                "GOLD_M30",
                "NO_SETUP",
                completed_bar_time=completed_bar_time,
                candidate_count=0,
                details={
                    "evaluation_code": evaluation.code,
                    "reason": evaluation.reason,
                    "signal_end": evaluation.signal_end,
                    "facts": evaluation.facts,
                },
            )
        return 0
    signal = LiveSignal(
        symbol=GOLD_SYMBOL, broker_symbol=broker_symbol, engine=ENGINE,
        setup="M30_BREAKOUT", mode="GOLD", side=setup.side.value,
        signal_time=setup.signal_end, requested_risk_percent=float(risk_percent),
        stop_pips=float(setup.stop_pips), target_pips=float(setup.target_pips),
        metadata={"reason": setup.reason, "engine_group": "GOLD"})
    now = datetime.now(timezone.utc)
    result = executor.place(signal, now=now)
    payload = {
        "created_at": now.isoformat(),
        "scan_scope": "GOLD_M30",
        "signal_key": signal.key,
        "signal": asdict(signal),
        "result": asdict(result),
    }
    _append_jsonl(EXECUTION_LOG, payload)
    if audit is not None:
        audit.record(
            "GOLD_M30",
            "CANDIDATE_PROCESSED",
            completed_bar_time=completed_bar_time,
            candidate_count=1,
            details={
                "evaluation_code": evaluation.code,
                "reason": evaluation.reason,
                "signal_time": signal.signal_time,
                "execution_code": result.code,
                "execution_message": result.message,
                "completed_m30_shift": completed_m30_shift,
            },
        )
    if recent_decisions is not None:
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
    return 1 if getattr(result, "ok", False) and getattr(result, "ticket", None) else 0


def _scan_gold_pullback_shadow_once(
    client: Any,
    broker_symbol: str,
    *,
    completed_m30_shift: int = 0,
    completed_bar_time: int | None = None,
    audit: ScanAuditJournal | None = None,
) -> None:
    """Journal the independent Gold pullback engine without sending orders."""
    from mt5_ai_bridge.gold_trend_pullback_engine import (
        AUDIT_SCOPE,
        evaluate_gold_pullback_diagnostic,
    )

    evaluation = evaluate_gold_pullback_diagnostic(
        client,
        broker_symbol,
        completed_m30_shift=completed_m30_shift,
    )
    if audit is not None:
        audit.record(
            AUDIT_SCOPE,
            (
                "SHADOW_CANDIDATE"
                if evaluation.setup is not None
                else "NO_SETUP"
            ),
            completed_bar_time=completed_bar_time,
            candidate_count=1 if evaluation.setup is not None else 0,
            details={
                "evaluation_code": evaluation.code,
                "reason": evaluation.reason,
                "signal_end": evaluation.signal_end,
                "facts": evaluation.facts,
                "execution_authority": "SHADOW_ONLY",
                "order_sent": False,
            },
        )


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
    order_flow_monitor = OrderFlowMonitor(
        refresh_seconds=float(os.getenv("V14_21_ORDER_FLOW_REFRESH_SECONDS", "15"))
    )
    scan_audit = ScanAuditJournal(
        cursor_path=os.getenv(
            "V14_21_SCAN_CURSOR_PATH", "state/v14_21_scan_cursors.json"
        ),
        event_path=os.getenv(
            "V14_21_SCAN_EVENT_PATH", "state/v14_21_scan_events.jsonl"
        ),
    )
    gold_catch_up_bars = max(
        2, int(os.getenv("V14_21_GOLD_CATCH_UP_BARS", "48"))
    )
    # Metals satellite (OPT-IN): set GOLD_ENGINE=on to add the validated gold
    # intraday breakout on completed M30 candles, routed through this executor.
    gold_enabled = os.getenv("GOLD_ENGINE", "").strip().lower() in {
        "1", "true", "yes", "on"}
    gold_risk = float(os.getenv("GOLD_RISK_PERCENT", "0.25") or 0.25)
    gold_broker: str | None = None
    gold_symbol = "XAUUSD"
    if gold_enabled:
        try:
            from mt5_ai_bridge.gold_intraday_engine import GOLD_SYMBOL as gold_symbol
            from mt5_ai_bridge.v14_3_live_execution import (
                resolve_broker_symbol as _resolve_gold)
            gold_broker = _resolve_gold(client, gold_symbol)
            print(f"[gold] metals engine ARMED: {gold_symbol} -> broker "
                  f"'{gold_broker}' | risk {gold_risk}% | scans completed M30 "
                  f"candles alongside the FX engines.", flush=True)
        except Exception as exc:  # noqa: BLE001
            gold_enabled = False
            print(f"[gold] DISABLED: could not resolve '{gold_symbol}' on your "
                  f"broker ({exc}). Add gold to MT5 Market Watch, or set its exact "
                  f"broker name. The FX engines continue normally.", flush=True)
    elif os.getenv("GOLD_ENGINE"):
        print("[gold] GOLD_ENGINE is set but not enabled — use on/true/1.",
              flush=True)
    last_m30_signature: tuple[tuple[str, int | None], ...] | None = None
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
        "scan_schedule": {
            "FX_PORTFOLIO": {
                "trigger": "H1",
                "timeframes": ["H1", "H4", "D1"],
                "last_scan_at": None,
            },
            "GBP_ICT": {
                "trigger": "M1",
                "timeframes": ["M1"],
                "last_scan_at": None,
            },
            "GOLD": {
                "trigger": "M30",
                "timeframes": ["M15", "M30", "H4"],
                "last_scan_at": None,
                "enabled": gold_enabled,
            },
        },
        "order_flow_mode": "OBSERVE_ONLY",
        "order_flow": [],
        "order_flow_shadow_mode": getattr(
            config, "order_flow_enforcement_mode", "SHADOW_ONLY"
        ),
        "order_flow_shadow": [],
        "scan_audit": scan_audit.snapshot(),
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
                    prior_schedule = dict(diagnostics.get("scan_schedule") or {})
                    _record_signature_gaps(
                        scan_audit,
                        "FX_PORTFOLIO_H1",
                        h1_signature,
                        timeframe_seconds=3600,
                        recovery_bars=LOOKBACK_HOURS,
                    )
                    scan_audit.record(
                        "FX_PORTFOLIO_H1",
                        "SCAN_STARTED",
                        completed_bar_time=max(
                            (
                                marker for _symbol, marker in h1_signature
                                if marker is not None
                            ),
                            default=None,
                        ),
                        details={"bars": _signature_details(h1_signature)},
                    )
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
                    scanned_at = datetime.now(timezone.utc).isoformat()
                    diagnostics["last_h1_strategy_scan_at"] = scanned_at
                    diagnostics["last_gbp_ict_scan_at"] = scanned_at
                    diagnostics["scan_schedule"] = prior_schedule
                    diagnostics["scan_schedule"]["FX_PORTFOLIO"] = {
                        "trigger": "H1",
                        "timeframes": ["H1", "H4", "D1"],
                        "last_scan_at": scanned_at,
                    }
                    diagnostics["scan_schedule"]["GBP_ICT"] = {
                        "trigger": "M1",
                        "timeframes": ["M1"],
                        "last_scan_at": scanned_at,
                    }
                    scan_audit.record(
                        "FX_PORTFOLIO_H1",
                        "SCAN_COMPLETED",
                        completed_bar_time=max(
                            (
                                marker for _symbol, marker in h1_signature
                                if marker is not None
                            ),
                            default=None,
                        ),
                        candidate_count=int(
                            diagnostics.get("candidate_count", 0) or 0
                        ),
                        details={
                            "generation": diagnostics.get("generation") or {},
                            "engine_statuses": {
                                str(item.get("engine")): str(item.get("status"))
                                for item in diagnostics.get("engines") or []
                            },
                        },
                    )
                    _advance_signature_cursors(
                        scan_audit, "FX_PORTFOLIO_H1", h1_signature
                    )
                    _advance_signature_cursors(
                        scan_audit, "GBP_ICT_M1", m1_signature
                    )
                    diagnostics["strategy_state"] = "WAITING"
                elif new_m1:
                    diagnostics["strategy_state"] = "SCANNING M1 ICT"
                    dashboard.write(diagnostics)
                    _record_signature_gaps(
                        scan_audit,
                        "GBP_ICT_M1",
                        m1_signature,
                        timeframe_seconds=60,
                        recovery_bars=90,
                    )
                    scan_audit.record(
                        "GBP_ICT_M1",
                        "SCAN_STARTED",
                        completed_bar_time=max(
                            (
                                marker for _symbol, marker in m1_signature
                                if marker is not None
                            ),
                            default=None,
                        ),
                        details={"bars": _signature_details(m1_signature)},
                    )
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
                    scanned_at = datetime.now(timezone.utc).isoformat()
                    diagnostics["last_gbp_ict_scan_at"] = scanned_at
                    diagnostics.setdefault("scan_schedule", {})["GBP_ICT"] = {
                        "trigger": "M1",
                        "timeframes": ["M1"],
                        "last_scan_at": scanned_at,
                    }
                    scan_audit.record(
                        "GBP_ICT_M1",
                        "SCAN_COMPLETED",
                        completed_bar_time=max(
                            (
                                marker for _symbol, marker in m1_signature
                                if marker is not None
                            ),
                            default=None,
                        ),
                        candidate_count=int(
                            update.get("candidate_count", 0) or 0
                        ),
                        details={
                            "provider": (
                                update.get("generation") or {}
                            ).get("legacy_gbp_ict_provider"),
                            "execution_results": [
                                item.get("code")
                                for symbol in GBP_ICT_SYMBOLS
                                for item in (
                                    (update.get("symbols") or {})
                                    .get(symbol, {})
                                    .get("results", [])
                                )
                            ],
                        },
                    )
                    _advance_signature_cursors(
                        scan_audit, "GBP_ICT_M1", m1_signature
                    )
                    diagnostics["strategy_state"] = "WAITING"

                # Metals satellite: independent M30 gold scan (opt-in).
                if gold_enabled and gold_broker:
                    m30_signature = _closed_bar_signature(
                        client, {gold_symbol: gold_broker}, "M30", [gold_symbol])
                    pending_gold = _pending_gold_bars(
                        client, gold_broker, scan_audit, gold_catch_up_bars
                    )
                    if pending_gold:
                        for completed_bar_time, shift in pending_gold:
                            scan_audit.record(
                                "GOLD_M30",
                                "SCAN_STARTED",
                                completed_bar_time=completed_bar_time,
                                details={
                                    "completed_m30_shift": shift,
                                    "catch_up": shift > 0,
                                },
                            )
                            with (
                                contextlib.redirect_stdout(io.StringIO()),
                                contextlib.redirect_stderr(io.StringIO()),
                            ):
                                trades_placed += _scan_gold_once(
                                    client,
                                    executor,
                                    gold_broker,
                                    gold_risk,
                                    recent_decisions,
                                    completed_m30_shift=shift,
                                    completed_bar_time=completed_bar_time,
                                    audit=scan_audit,
                                )
                                _scan_gold_pullback_shadow_once(
                                    client,
                                    gold_broker,
                                    completed_m30_shift=shift,
                                    completed_bar_time=completed_bar_time,
                                    audit=scan_audit,
                                )
                            scan_audit.set_cursor(
                                "GOLD_M30", completed_bar_time
                            )
                        last_m30_signature = m30_signature
                        scanned_at = datetime.now(timezone.utc).isoformat()
                        diagnostics["last_gold_scan_at"] = scanned_at
                        diagnostics.setdefault("scan_schedule", {})["GOLD"] = {
                            "trigger": "M30",
                            "timeframes": ["M15", "M30", "H4"],
                            "last_scan_at": scanned_at,
                            "enabled": True,
                        }
                    # Keep gold visible in the live engine list every loop (the
                    # FX scan rebuilds 'engines' without it, so re-add it here).
                    _eng = [
                        e for e in (diagnostics.get("engines") or [])
                        if e.get("engine") not in {
                            "GOLD_INTRADAY_M30",
                            "GOLD_TREND_PULLBACK_M30",
                        }
                    ]
                    _gold_decision = next(
                        (
                            item for item in recent_decisions
                            if item.get("engine") == "GOLD_INTRADAY_M30"
                        ),
                        None,
                    )
                    _gold_status = "WAITING"
                    _gold_rationale = (
                        "Gold M30 breakout engine active "
                        "(07:00-17:00 UTC session entries)."
                    )
                    _gold_audit = (
                        scan_audit.snapshot()
                        .get("latest_by_scope", {})
                        .get("GOLD_M30")
                    )
                    if _gold_audit:
                        _restore_persistent_scan_time(
                            diagnostics,
                            "GOLD",
                            _gold_audit,
                        )
                        _details = _gold_audit.get("details") or {}
                        _gold_rationale = (
                            f"Last completed M30 evaluation: "
                            f"{_details.get('evaluation_code', _gold_audit.get('outcome'))}. "
                            f"{_details.get('reason', '')}"
                        ).strip()
                    if _gold_decision is not None:
                        _gold_code = str(_gold_decision.get("code", "UNKNOWN"))
                        _gold_status = (
                            "LAST_FILLED"
                            if _gold_code == "ORDER_FILLED"
                            else "LAST_REJECTED"
                        )
                        _gold_rationale = (
                            f"Latest candidate at {_gold_decision.get('time', 'unknown time')} "
                            f"finished with {_gold_code}. "
                            f"{_gold_decision.get('rationale', '')}"
                        ).strip()
                    _eng.append({
                        "symbol": gold_symbol, "mode": "GOLD",
                        "engine": "GOLD_INTRADAY_M30",
                        "status": _gold_status,
                        "rationale": _gold_rationale,
                    })
                    _pullback_audit = (
                        scan_audit.snapshot()
                        .get("latest_by_scope", {})
                        .get("GOLD_PULLBACK_M30")
                    )
                    _pullback_rationale = (
                        "Independent H4/M30/M15 trend-pullback scanner; "
                        "SHADOW ONLY because 0/96 historical variants passed "
                        "development and confirmation profitability gates."
                    )
                    if _pullback_audit:
                        _pullback_details = (
                            _pullback_audit.get("details") or {}
                        )
                        _pullback_rationale = (
                            f"SHADOW ONLY — last completed M30 evaluation: "
                            f"{_pullback_details.get('evaluation_code', _pullback_audit.get('outcome'))}. "
                            f"{_pullback_details.get('reason', '')}"
                        ).strip()
                    _eng.append({
                        "symbol": gold_symbol,
                        "mode": "GOLD_SHADOW",
                        "engine": "GOLD_TREND_PULLBACK_M30",
                        "status": "SHADOW_ONLY",
                        "rationale": _pullback_rationale,
                    })
                    diagnostics["engines"] = _eng

                flow_symbols = dict(broker_map)
                if gold_enabled and gold_broker:
                    flow_symbols[gold_symbol] = gold_broker
                diagnostics["order_flow"] = order_flow_monitor.snapshot(
                    client, flow_symbols
                )
                diagnostics["order_flow_mode"] = "OBSERVE_ONLY"
                diagnostics["order_flow_shadow_mode"] = getattr(
                    executor.config,
                    "order_flow_enforcement_mode",
                    "SHADOW_ONLY",
                )
                diagnostics["order_flow_shadow"] = list(
                    getattr(executor, "recent_order_flow_shadow", [])
                )
                diagnostics["order_flow_forward"] = list(
                    getattr(
                        executor,
                        "order_flow_forward_snapshot",
                        lambda: [],
                    )()
                )
                diagnostics["futures_order_flow"] = list(
                    getattr(
                        executor,
                        "futures_order_flow",
                        None,
                    ).snapshot()
                    if getattr(executor, "futures_order_flow", None)
                    is not None
                    else []
                )
                diagnostics["scan_audit"] = scan_audit.snapshot()
                diagnostics["engines"] = apply_engine_runtime_metadata(
                    list(diagnostics.get("engines") or []),
                    diagnostics.get("scan_schedule") or {},
                )
                diagnostics["runner_wiring"] = {
                    "executor": type(executor).__name__,
                    "automatic_runner_connected": True,
                    "connected_engine_count": len(diagnostics["engines"]),
                    "order_authorized_engine_count": sum(
                        item.get("status") != "SHADOW_ONLY"
                        for item in diagnostics["engines"]
                    ),
                    "shadow_engine_count": sum(
                        item.get("status") == "SHADOW_ONLY"
                        for item in diagnostics["engines"]
                    ),
                    "order_path": (
                        "validated order-authorized candidates -> universal "
                        "side-aware order flow -> executor.place; unvalidated "
                        "engines journal shadow candidates only"
                    ),
                }

                diagnostics = _heartbeat_snapshot(
                    client,
                    executor,
                    diagnostics,
                    trades_placed,
                )
                dashboard.write(diagnostics)
                _print_status(diagnostics, trades_placed=trades_placed)
            except Exception as exc:  # noqa: BLE001
                scan_audit.record(
                    "RUNNER_LOOP",
                    "SCAN_ERROR",
                    details={
                        "strategy_state": diagnostics.get("strategy_state"),
                    },
                    error=f"{type(exc).__name__}: {exc}",
                )
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
