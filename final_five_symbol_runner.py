"""Final five-symbol demo runner with per-symbol diagnostics.

This runner executes only the approved V12 engine registry. It writes a complete
symbol health report on every scan, including symbols that generated no trade.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import v12_final_runner as legacy
from mt5_ai_bridge.app import connect
from mt5_ai_bridge.config import load_settings
from mt5_ai_bridge.execution import pip_size
from mt5_ai_bridge.final_engine_registry import FINAL_ENGINES, FINAL_SYMBOLS, registry_summary
from mt5_ai_bridge.mt5_client import create_client
from mt5_ai_bridge.symbol_diagnostics import build_diagnostics, write_diagnostics
from mt5_ai_bridge.v12_final_adapter import FinalV12Adapter, NamedEngineSignal
from mt5_ai_bridge.v12_final_mode import AccountModeStore
from mt5_ai_bridge.v12_final_risk import ENGINE_RULES


ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "final_five_symbol_runner_state.json"
EXECUTION_LOG = ROOT / "final_five_symbol_executions.jsonl"
DIAGNOSTICS_FILE = ROOT / "final_symbol_diagnostics.json"
ENGINE_REGISTRY_FILE = ROOT / "final_engine_registry.json"


def validate_final_registry() -> None:
    approved = set(FINAL_ENGINES)
    executable = set(ENGINE_RULES)
    if approved != executable:
        missing = sorted(approved - executable)
        extra = sorted(executable - approved)
        raise RuntimeError(
            f"Final engine registry mismatch; missing executable={missing}, "
            f"unregistered executable={extra}"
        )
    exit_engines = {engine for engine, _setup in legacy.EXIT_MAP}
    if approved != exit_engines:
        raise RuntimeError(
            "Every approved engine must have a frozen stop/target configuration."
        )


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def scan_once(
    client,
    adapter: FinalV12Adapter,
    state_path: Path = STATE_FILE,
    execution_log: Path = EXECUTION_LOG,
    diagnostics_path: Path = DIAGNOSTICS_FILE,
    lookback_hours: int = 8,
) -> tuple[list[dict], list[dict]]:
    now = datetime.now(timezone.utc)
    prepared = {}
    preparation_errors: dict[str, str] = {}
    for symbol in FINAL_SYMBOLS:
        try:
            prepared[symbol] = legacy.prepare_live_frames(client, symbol)
        except Exception as exc:  # noqa: BLE001
            preparation_errors[symbol] = f"{type(exc).__name__}: {exc}"
            prepared[symbol] = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    available = {
        symbol: frames for symbol, frames in prepared.items()
        if all(not frame.empty for frame in frames)
    }
    candidates = (
        legacy.build_final_candidates(available)
        if len(available) == len(FINAL_SYMBOLS)
        else pd.DataFrame()
    )

    cutoff = pd.Timestamp(now) - pd.Timedelta(hours=lookback_hours)
    recent = (
        candidates[candidates["entry_time"] >= cutoff]
        if not candidates.empty
        else pd.DataFrame()
    )
    state = _load_state(state_path)
    seen = set(state.get("seen", []))
    emitted: list[dict] = []
    latest_results: dict[str, dict[str, str]] = {
        symbol: {"code": "DATA_UNAVAILABLE", "message": message}
        for symbol, message in preparation_errors.items()
    }

    for row in recent.itertuples(index=False):
        engine = str(row.engine)
        if engine not in FINAL_ENGINES:
            continue
        key = legacy._signal_key(row)
        if key in seen:
            latest_results[str(row.symbol)] = {
                "code": "DUPLICATE_SIGNAL_SEEN",
                "message": "This approved signal was already processed by the runner.",
            }
            continue
        stop_atr, target_r = legacy.EXIT_MAP[(engine, str(row.setup))]
        atr_value = legacy._atr_for_signal(prepared, row)
        pip = pip_size(client, str(row.symbol))
        if pip is None or not np.isfinite(atr_value) or atr_value <= 0:
            latest_results[str(row.symbol)] = {
                "code": "ATR_OR_PIP_UNAVAILABLE",
                "message": "The signal could not be sized because ATR or pip data was invalid.",
            }
            continue

        signal = NamedEngineSignal(
            symbol=str(row.symbol),
            engine=engine,
            setup=str(row.setup),
            side="BUY" if int(row.side) > 0 else "SELL",
            base_risk_percent=float(row.risk_percent),
            stop_pips=float(atr_value * stop_atr / pip),
            target_pips=float(atr_value * stop_atr * target_r / pip),
            signal_time=pd.Timestamp(row.entry_time).to_pydatetime(),
        )
        result = adapter.submit(signal, now=now)
        payload = {
            "created_at": now.isoformat(),
            "signal_key": key,
            "signal": asdict(signal),
            "result": {
                "ok": result.ok,
                "code": result.code,
                "message": result.message,
                "volume": result.volume,
                "risk_percent": result.risk_percent,
                "ticket": result.ticket,
                "proposal": asdict(result.proposal) if result.proposal else None,
            },
        }
        _append_jsonl(execution_log, payload)
        print(json.dumps(payload, indent=2, default=str))
        emitted.append(payload)
        latest_results[signal.symbol] = {
            "code": result.code,
            "message": result.message,
        }
        if result.ok:
            seen.add(key)

    state["seen"] = sorted(seen)[-5000:]
    _save_state(state_path, state)
    diagnostics = build_diagnostics(
        client=client,
        adapter=adapter,
        prepared=prepared,
        candidates=candidates,
        execution_results=latest_results,
        lookback_hours=lookback_hours,
        now=now,
    )
    write_diagnostics(diagnostics_path, diagnostics)
    diagnostic_payload = [asdict(item) for item in diagnostics]
    print(json.dumps({"symbol_diagnostics": diagnostic_payload}, indent=2))
    return emitted, diagnostic_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the final approved five-symbol strategy with diagnostics"
    )
    parser.add_argument("--once", action="store_true", help="Scan once and exit")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--lookback-hours", type=int, default=8)
    args = parser.parse_args()

    validate_final_registry()
    ENGINE_REGISTRY_FILE.write_text(
        json.dumps(registry_summary(), indent=2, sort_keys=True), encoding="utf-8"
    )
    settings = load_settings()
    client = create_client()
    connect(client, settings)
    mode_store = AccountModeStore(os.getenv(
        "V12_FINAL_ACCOUNT_MODE_PATH", "v12_final_account_mode.json"
    ))
    adapter = FinalV12Adapter(
        client,
        state_path=os.getenv("V12_FINAL_STATE_PATH", "v12_final_research_state.json"),
        max_deviation_points=int(os.getenv("V12_FINAL_MAX_DEVIATION_POINTS", "10")),
        account_mode_provider=mode_store.get,
    )
    try:
        while True:
            try:
                emitted, diagnostics = scan_once(
                    client,
                    adapter,
                    lookback_hours=args.lookback_hours,
                )
                if not emitted:
                    ready = [item["symbol"] for item in diagnostics if item["rejection_code"] == "CANDIDATE_READY"]
                    print(f"{datetime.now(timezone.utc).isoformat()} no filled orders; ready={ready}")
            except Exception as exc:  # noqa: BLE001
                print(f"final runner error: {type(exc).__name__}: {exc}")
            if args.once:
                break
            time.sleep(max(15, args.interval))
    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
