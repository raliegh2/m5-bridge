"""Concurrent read-only missed-signal audit for the active V12 final bot.

This script is safe to run while ``v12_final_bot.py`` is active. It connects to
MT5 only for closed-candle market data, rebuilds the same V12 final candidates,
and compares today's candidates against the runner's seen-state and execution
JSONL log.

It intentionally does NOT:
- call FinalV12Adapter.submit;
- call MT5 order_send;
- modify v12_final_runner_state.json;
- modify v12_final_executions.jsonl;
- modify any live trading state.

Example one-shot:
    python research/audit_today_missed_signals.py --since 2026-07-05T21:00:00Z

Example concurrent loop while bot runs:
    python research/audit_today_missed_signals.py --since 2026-07-05T21:00:00Z --loop --interval 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from mt5_ai_bridge.app import connect  # noqa: E402
from mt5_ai_bridge.config import load_settings  # noqa: E402
from mt5_ai_bridge.execution import pip_size  # noqa: E402
from mt5_ai_bridge.mt5_client import create_client  # noqa: E402
from mt5_ai_bridge.v12_final_risk import (  # noqa: E402
    ENGINE_RULES,
    OrderIntent,
    PortfolioSnapshot,
    make_order_key,
    validate_order,
)
from mt5_ai_bridge.v12_final_state import StateStore  # noqa: E402
from v12_final_runner import (  # noqa: E402
    EXIT_MAP,
    PROPOSAL_LOG,
    STATE_FILE,
    SYMBOLS,
    _atr_for_signal,
    _signal_key,
    build_final_candidates,
    prepare_live_frames,
)

OUT = RESEARCH / "audit_today_missed_signals_output"
OUT.mkdir(parents=True, exist_ok=True)


def _safe_json(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _timestamp(value: str | None) -> pd.Timestamp:
    if value:
        stamp = pd.Timestamp(value)
    else:
        now = pd.Timestamp.now(tz="UTC")
        stamp = now.normalize()
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _load_seen(path: Path = STATE_FILE) -> set[str]:
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(raw.get("seen", []))


def _load_execution_log(path: Path = PROPOSAL_LOG) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    rows: dict[str, list[dict]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = str(payload.get("signal_key", ""))
        if key:
            rows.setdefault(key, []).append(payload)
    return rows


def _side_label(side: int) -> str:
    return "BUY" if int(side) > 0 else "SELL"


def _market_context(client, symbol: str) -> tuple[float, float, float]:
    account = client.account_info()
    if account is None:
        raise RuntimeError("MT5 account_info() is unavailable; cannot audit gate context.")
    tick = client.symbol_info_tick(symbol)
    pip = pip_size(client, symbol)
    if tick is None or pip is None:
        raise RuntimeError(f"{symbol}: tick or pip data unavailable.")
    spread_pips = (float(tick.ask) - float(tick.bid)) / pip
    return float(account.balance), float(account.equity), float(spread_pips)


def _candidate_gate_status(client, state: StateStore, prepared: dict, row) -> tuple[bool, str, float]:
    """Best-effort read-only gate evaluation.

    The live runner performs broker sizing inside the executor. This audit uses a
    conservative synthetic volume that should not exceed the requested risk. It
    is meant to explain whether obvious risk/profile gates would block a signal,
    not to submit or size a real order.
    """
    symbol = str(row.symbol)
    engine = str(row.engine)
    setup = str(row.setup)
    side = _side_label(int(row.side))
    rule = ENGINE_RULES.get(engine)
    if rule is None:
        return False, "ENGINE_NOT_ALLOWED", 0.0
    try:
        stop_atr, _target_r = EXIT_MAP[(engine, setup)]
        atr_value = _atr_for_signal(prepared, row)
        pip = pip_size(client, symbol)
        if pip is None or atr_value <= 0:
            return False, "ATR_OR_PIP_UNAVAILABLE", 0.0
        stop_pips = float(atr_value * stop_atr / pip)
        balance, equity, spread_pips = _market_context(client, symbol)
    except Exception as exc:  # noqa: BLE001
        return False, f"AUDIT_CONTEXT_ERROR:{type(exc).__name__}", 0.0

    signal_time = pd.Timestamp(row.entry_time).to_pydatetime()
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=timezone.utc)
    order_key = make_order_key(symbol, engine, setup, side, signal_time)

    requested_risk = float(row.risk_percent)
    intent = OrderIntent(
        symbol=symbol,
        engine=engine,
        setup=setup,
        side=side,
        requested_risk_percent=requested_risk,
        guard_multiplier=1.0,
        stop_pips=stop_pips,
        # Synthetic low-risk volume/pip value pair. validate_order only needs
        # actual risk to be <= expected profile risk for this read-only audit.
        volume=0.01,
        pip_value_per_lot=max(0.01, balance * requested_risk / 100.0 / max(stop_pips, 1e-9) / 0.01 * 0.50),
        spread_pips=spread_pips,
        order_key=order_key,
    )
    snapshot = PortfolioSnapshot(
        balance=balance,
        equity=equity,
        day_start_equity=state.state.day_start_equity or equity,
        peak_equity=state.state.peak_equity or equity,
        open_risk=state.open_risk(),
        recent_order_keys=frozenset(state.state.recent_orders),
        now=datetime.now(timezone.utc),
    )
    decision = validate_order(intent, snapshot)
    return bool(decision.ok), str(decision.code), float(decision.actual_risk_percent)


def run_audit(since: pd.Timestamp, lookback_hours: int = 12) -> dict:
    settings = load_settings()
    client = create_client()
    try:
        connect(client, settings)
        prepared = {symbol: prepare_live_frames(client, symbol) for symbol in SYMBOLS}
        candidates = build_final_candidates(prepared)
        if candidates.empty:
            window = candidates
        else:
            window = candidates[candidates["entry_time"] >= since].copy()
            window = window.sort_values(["entry_time", "engine", "setup"]).reset_index(drop=True)

        seen = _load_seen()
        logged = _load_execution_log()
        state = StateStore(os.getenv("V12_FINAL_STATE_PATH", "v12_final_research_state.json"))

        rows: list[dict] = []
        for row in window.itertuples(index=False):
            key = _signal_key(row)
            logged_rows = logged.get(key, [])
            gate_ok, gate_code, actual_risk = _candidate_gate_status(client, state, prepared, row)
            in_seen = key in seen
            in_log = bool(logged_rows)
            latest_result = logged_rows[-1].get("result", {}) if logged_rows else {}
            eligible = gate_ok
            missed = bool(eligible and not in_seen and not in_log)
            rows.append(
                {
                    "signal_key": key,
                    "symbol": str(row.symbol),
                    "engine": str(row.engine),
                    "setup": str(row.setup),
                    "side": _side_label(int(row.side)),
                    "entry_time": pd.Timestamp(row.entry_time).isoformat(),
                    "exit_time": pd.Timestamp(row.exit_time).isoformat(),
                    "risk_percent": float(row.risk_percent),
                    "r_multiple_research_only": float(row.r_multiple),
                    "eligible_now_best_effort": eligible,
                    "gate_code_now_best_effort": gate_code,
                    "actual_risk_percent_best_effort": actual_risk,
                    "seen_in_runner_state": in_seen,
                    "logged_in_execution_jsonl": in_log,
                    "latest_log_code": latest_result.get("code", ""),
                    "latest_log_ok": latest_result.get("ok", ""),
                    "missed_eligible_candidate": missed,
                }
            )

        audit = pd.DataFrame(rows)
        output_csv = OUT / "missed_signal_audit.csv"
        audit.to_csv(output_csv, index=False)

        summary = {
            "status": "PASS",
            "audit_time_utc": datetime.now(timezone.utc).isoformat(),
            "since": since.isoformat(),
            "candidate_count_since": int(len(audit)),
            "eligible_now_best_effort": int(audit["eligible_now_best_effort"].sum()) if not audit.empty else 0,
            "logged_proposal_or_order_attempts": int(audit["logged_in_execution_jsonl"].sum()) if not audit.empty else 0,
            "seen_in_runner_state": int(audit["seen_in_runner_state"].sum()) if not audit.empty else 0,
            "missed_eligible_candidates": int(audit["missed_eligible_candidate"].sum()) if not audit.empty else 0,
            "missed": bool(int(audit["missed_eligible_candidate"].sum()) if not audit.empty else 0),
            "output_csv": str(output_csv),
            "notes": [
                "Read-only audit: no orders are sent.",
                "Gate status is best-effort using current account/spread/state, so historical gate context can differ.",
                "r_multiple_research_only is diagnostic and not used to decide eligibility.",
            ],
        }
        (OUT / "missed_signal_audit_summary.json").write_text(
            json.dumps(summary, indent=2, default=_safe_json), encoding="utf-8"
        )

        report_lines = [
            "# Missed Signal Audit Report",
            "",
            "Status: read-only concurrent audit; no orders submitted and no bot state modified.",
            "",
            f"- Audit time UTC: {summary['audit_time_utc']}",
            f"- Since: {summary['since']}",
            f"- Candidates since window start: {summary['candidate_count_since']}",
            f"- Eligible now, best effort: {summary['eligible_now_best_effort']}",
            f"- Logged proposal/order attempts: {summary['logged_proposal_or_order_attempts']}",
            f"- Seen in runner state: {summary['seen_in_runner_state']}",
            f"- Missed eligible candidates: {summary['missed_eligible_candidates']}",
            "",
            "## Interpretation",
            "",
            "A candidate is flagged as missed only when it is currently gate-eligible in this best-effort audit and is absent from both the execution JSONL and runner seen-state.",
            "Historical spreads/account state may differ from current audit state, so any missed flag should be reviewed before assuming a bot defect.",
        ]
        (OUT / "MISSED_SIGNAL_AUDIT_REPORT.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        return summary
    finally:
        client.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only audit for missed V12 final signals")
    parser.add_argument("--since", help="UTC start timestamp, e.g. 2026-07-05T21:00:00Z. Defaults to today's UTC midnight.")
    parser.add_argument("--loop", action="store_true", help="Run continuously while bot is active.")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between loop runs.")
    parser.add_argument("--lookback-hours", type=int, default=12, help="Reserved for future live-candidate audit windows.")
    args = parser.parse_args()

    since = _timestamp(args.since)
    while True:
        summary = run_audit(since=since, lookback_hours=args.lookback_hours)
        print(json.dumps(summary, indent=2, default=_safe_json))
        if not args.loop:
            break
        time.sleep(max(15, int(args.interval)))


if __name__ == "__main__":
    main()
