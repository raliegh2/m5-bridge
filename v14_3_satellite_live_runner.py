"""Local MT5 runner for the enhanced V12 + V14.3 satellite portfolio.

The runner has a one-second target loop, updates a localhost dashboard each cycle,
and defaults to READ_ONLY. AUTO remains restricted by the execution module's demo
and forward-validation gates.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.v14_3_live_dashboard import LiveDashboard
from mt5_ai_bridge.v14_3_live_execution import (
    MAGIC_BY_ENGINE,
    LiveRunnerConfig,
    SatelliteLiveExecutor,
)
from mt5_ai_bridge.v14_3_live_signals import build_all_live_signals

ROOT = Path(__file__).resolve().parent
DIAGNOSTICS = ROOT / "v14_3_satellite_live_diagnostics.json"
DASHBOARD_SNAPSHOT = ROOT / "state" / "v14_3_satellite_dashboard.json"
EXECUTION_LOG = ROOT / "v14_3_satellite_live_executions.jsonl"

ENGINE_REGISTRY = (
    ("GBPUSD", "V12", "GBPUSD_V10_PRECISION"),
    ("GBPUSD", "V12", "GBPUSD_SWING_RETEST"),
    ("EURUSD", "V12", "EURUSD_SWING_CORE"),
    ("EURUSD", "V12", "EURUSD_SWING_RETEST"),
    ("GBPJPY", "V12", "GBPJPY_SWING_CORE"),
    ("AUDUSD", "V12", "AUDUSD_TREND_PULLBACK"),
    ("USDJPY", "V12", "USDJPY_SAFE_HAVEN_BREAKOUT"),
    ("EURUSD", "ICT", "EURUSD_ICT_LIQUIDITY"),
    ("AUDUSD", "ICT", "AUDUSD_ICT_ASIA_LONDON"),
    ("USDJPY", "ICT", "USDJPY_ICT_SESSION_SWEEP"),
    ("GBPUSD", "ICT", "ICT_V14_3_GBPUSD"),
    ("GBPJPY", "ICT", "ICT_V14_3_GBPJPY"),
)
ENGINE_BY_MAGIC = {magic: engine for engine, magic in MAGIC_BY_ENGINE.items()}


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _position_snapshot(client: Any, executor: SatelliteLiveExecutor) -> list[dict[str, Any]]:
    stored = executor.state.data.get("positions", {})
    rows: list[dict[str, Any]] = []
    for position in client.positions_get() or []:
        ticket = int(getattr(position, "ticket", 0) or 0)
        persisted = stored.get(str(ticket), {})
        magic = int(getattr(position, "magic", 0) or 0)
        engine = str(
            persisted.get("engine")
            or ENGINE_BY_MAGIC.get(magic)
            or f"MAGIC_{magic}"
        )
        position_type = int(getattr(position, "type", -1))
        buy_type = int(getattr(client, "POSITION_TYPE_BUY", 0))
        rows.append({
            "ticket": ticket,
            "symbol": str(getattr(position, "symbol", "")),
            "engine": engine,
            "mode": persisted.get("mode"),
            "side": "BUY" if position_type == buy_type else "SELL",
            "volume": float(getattr(position, "volume", 0.0) or 0.0),
            "price_open": float(getattr(position, "price_open", 0.0) or 0.0),
            "price_current": float(getattr(position, "price_current", 0.0) or 0.0),
            "sl": float(getattr(position, "sl", 0.0) or 0.0),
            "tp": float(getattr(position, "tp", 0.0) or 0.0),
            "profit": float(getattr(position, "profit", 0.0) or 0.0),
        })
    return rows


def _decision_rationale(signal: Any, result: Any) -> str:
    metadata = dict(signal.metadata or {})
    source = metadata.get("source", "configured engine")
    timeframe = metadata.get("timeframe")
    profile = metadata.get("profile")
    facts = [f"{signal.engine} produced a {signal.side} {signal.setup} signal"]
    facts.append(f"from {source}")
    if timeframe:
        facts.append(f"using the completed {timeframe} candle")
    if profile:
        facts.append(f"under profile {profile}")
    facts.append(
        f"with requested risk {signal.requested_risk_percent:.3f}%, "
        f"SL {signal.stop_pips:.1f} pips and TP {signal.target_pips:.1f} pips"
    )
    facts.append(f"Final control result: {result.code} — {result.message}")
    return "; ".join(facts) + "."


def _engine_status(
    signals: list[Any],
    results: list[dict[str, Any]],
    generation: dict[str, Any],
) -> list[dict[str, Any]]:
    signaled = {signal.engine for signal in signals}
    result_by_engine = {
        item["signal"]["engine"]: item["result"]["code"] for item in results
    }
    legacy = str(generation.get("legacy_gbp_ict_provider", "UNKNOWN"))
    statuses: list[dict[str, Any]] = []
    for symbol, mode, engine in ENGINE_REGISTRY:
        if engine in signaled:
            code = result_by_engine.get(engine, "SIGNAL")
            status = "SIGNAL"
            rationale = f"Candidate matched; latest execution control result is {code}."
        elif mode == "ICT" and symbol in {"GBPUSD", "GBPJPY"} and legacy != "READY":
            status = "PROVIDER_WAIT"
            rationale = f"Legacy GBP ICT provider status: {legacy}."
        elif (mode == "V12" and generation.get("v12_error")) or (
            mode == "ICT" and symbol in {"EURUSD", "AUDUSD", "USDJPY"}
            and generation.get("satellite_ict_error")
        ):
            status = "GENERATION_ERROR"
            rationale = str(
                generation.get("v12_error")
                if mode == "V12"
                else generation.get("satellite_ict_error")
            )
        else:
            status = "WAITING"
            rationale = "Engine active; no completed-candle setup matched this scan."
        statuses.append({
            "symbol": symbol,
            "mode": mode,
            "engine": engine,
            "status": status,
            "rationale": rationale,
        })
    return statuses


def scan_once(
    client: Any,
    executor: SatelliteLiveExecutor,
    lookback_hours: int = 8,
    recent_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    now = datetime.now(timezone.utc)
    signals, generation = build_all_live_signals(
        client,
        lookback_hours=lookback_hours,
    )
    results: list[dict[str, Any]] = []
    decisions = recent_decisions if recent_decisions is not None else []

    for signal in signals:
        result = executor.place(signal, now=now)
        payload = {
            "created_at": now.isoformat(),
            "signal_key": signal.key,
            "signal": asdict(signal),
            "result": asdict(result),
        }
        results.append(payload)
        _append_jsonl(EXECUTION_LOG, payload)
        print(json.dumps(payload, indent=2, default=str))
        decisions.insert(0, {
            "time": now.isoformat(),
            "symbol": signal.symbol,
            "engine": signal.engine,
            "setup": signal.setup,
            "mode": signal.mode,
            "side": signal.side,
            "risk_percent": round(float(result.risk_percent or signal.requested_risk_percent), 4),
            "ok": bool(result.ok),
            "code": result.code,
            "rationale": _decision_rationale(signal, result),
            "metadata": dict(signal.metadata or {}),
        })
    del decisions[100:]

    account = client.account_info()
    positions = _position_snapshot(client, executor)
    per_symbol: dict[str, Any] = {}
    for symbol in ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"):
        symbol_signals = [item for item in signals if item.symbol == symbol]
        symbol_results = [
            item for item in results if item["signal"]["symbol"] == symbol
        ]
        per_symbol[symbol] = {
            "v12_candidates": sum(item.mode == "V12" for item in symbol_signals),
            "ict_candidates": sum(item.mode == "ICT" for item in symbol_signals),
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

    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
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
        "decisions": list(decisions),
        "symbols": per_symbol,
        "state_path": executor.config.state_path,
        "scan_latency_ms": latency_ms,
        "next_scan_seconds": 1,
    }
    _atomic_json(DIAGNOSTICS, diagnostics)
    print(json.dumps({
        "scan": now.isoformat(),
        "mode": executor.config.execution_mode,
        "candidates": len(signals),
        "orders_filled": diagnostics["orders_filled"],
        "open_positions": len(positions),
        "balance": diagnostics["account"]["balance"],
        "equity": diagnostics["account"]["equity"],
        "latency_ms": latency_ms,
    }, default=str))
    return diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the enhanced V14.3 satellite portfolio locally"
    )
    parser.add_argument("--once", action="store_true", help="Scan once and exit")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Target seconds between market scans (minimum 1 second)",
    )
    parser.add_argument("--lookback-hours", type=int, default=8)
    parser.add_argument(
        "--dashboard-host",
        default=os.getenv("V14_3_DASHBOARD_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=int(os.getenv("V14_3_DASHBOARD_PORT", "8814")),
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Do not launch the local dashboard server",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start dashboard server without opening the browser",
    )
    args = parser.parse_args()

    interval = max(1.0, float(args.interval))
    config = LiveRunnerConfig.from_env()
    settings = load_settings()
    client = create_client()
    connect(client, settings)
    executor = SatelliteLiveExecutor(client, config)
    dashboard: LiveDashboard | None = None
    recent_decisions: list[dict[str, Any]] = []

    if not args.no_dashboard:
        dashboard = LiveDashboard(
            snapshot_path=DASHBOARD_SNAPSHOT,
            host=args.dashboard_host,
            port=args.dashboard_port,
        )
        dashboard.write({
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
        })
        dashboard.start(open_browser=not args.no_browser)
        print(f"Dashboard: {dashboard.url}")

    try:
        next_scan = time.monotonic()
        while True:
            try:
                diagnostics = scan_once(
                    client,
                    executor,
                    lookback_hours=args.lookback_hours,
                    recent_decisions=recent_decisions,
                )
                diagnostics["next_scan_seconds"] = interval
                if dashboard is not None:
                    dashboard.write(diagnostics)
                if diagnostics["candidate_count"] == 0:
                    print(
                        f"{datetime.now(timezone.utc).isoformat()} "
                        "no new completed-candle signals"
                    )
            except Exception as exc:  # noqa: BLE001
                error = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "runner_status": "ERROR",
                    "execution_mode": config.execution_mode,
                    "code": "RUNNER_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "account": {},
                    "positions": [],
                    "engines": [],
                    "decisions": recent_decisions,
                    "generation": {},
                    "candidate_count": 0,
                    "scan_latency_ms": 0,
                }
                _append_jsonl(EXECUTION_LOG, error)
                print(json.dumps(error, indent=2))
                if dashboard is not None:
                    dashboard.write(error)
            if args.once:
                break
            next_scan += interval
            delay = next_scan - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # A complete signal evaluation can take longer than one second.
                # Reset the schedule instead of accumulating an ever-growing lag.
                next_scan = time.monotonic()
    finally:
        if dashboard is not None:
            dashboard.stop()
        client.shutdown()


if __name__ == "__main__":
    main()
