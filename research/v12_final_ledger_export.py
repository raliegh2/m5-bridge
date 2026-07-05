"""Export the V12 Final $3,201.58 backtest ledger.

This script reuses the exact V12 Final parity path:

- build final candidates through ``v12_final_runner.build_final_candidates``;
- compare candidates against the targeted weak-engine optimized candidate set;
- replay with ``targeted.targeted_guard_decision``;
- assert the known final result;
- export a chronological closed-trade ledger, equity curve, candidates,
  accepted trades, rejected trades, and summary JSON.

It sends no orders and imports no MT5 execution client.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "research"))

import v12_plus_validated_assets_backtest as study  # noqa: E402
import v12_targeted_weak_engine_optimization as targeted  # noqa: E402
from v12_final_runner import build_final_candidates  # noqa: E402

OUT = ROOT / "research" / "v12_final_ledger_output"
OUT.mkdir(parents=True, exist_ok=True)

EXPECTED_NET_PROFIT = 3201.58
EXPECTED_TRADES = 918
EXPECTED_PROFIT_FACTOR = 1.606
EXPECTED_MAX_DD_PERCENT = 4.93
PROFIT_TOLERANCE = 0.05
PF_TOLERANCE = 0.01
DD_TOLERANCE = 0.10


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _normalized_keys(frame: pd.DataFrame) -> set[tuple]:
    if frame.empty:
        return set()
    return {
        (
            str(row.symbol),
            str(row.engine),
            str(row.setup),
            int(row.side),
            pd.Timestamp(row.entry_time).isoformat(),
            float(row.risk_percent),
        )
        for row in frame.itertuples(index=False)
    }


def _drawdown_percent(peak: float, equity: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100.0)


def _side_label(side: int) -> str:
    return "BUY" if int(side) > 0 else "SELL"


def replay_with_ledger(
    candidates: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    config: study.PortfolioConfig,
    guard: study.GuardConfig = study.GuardConfig(),
) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay V12 Final and record close-time ledger rows.

    The position-admission logic mirrors ``study._replay`` while adding per-trade
    equity-before/equity-after values needed for a merged V12+ICT portfolio
    replay. Price columns are left blank because the V12 candidate layer stores
    R-multiple outcomes rather than broker fill prices.
    """
    data = candidates[(candidates["entry_time"] >= start) & (candidates["entry_time"] <= end)].copy()
    data = data.sort_values(["entry_time", "engine", "setup"]).reset_index(drop=True)

    balance = peak = float(study.STARTING_BALANCE)
    max_dd = stress_dd = 0.0
    active: list[dict] = []
    accepted: list[dict] = []
    rejected: list[dict] = []
    ledger: list[dict] = []
    equity_rows: list[dict] = [
        {
            "time": pd.Timestamp(start).isoformat(),
            "event": "START",
            "equity": balance,
            "balance": balance,
            "peak_equity": peak,
            "drawdown_percent": 0.0,
        }
    ]
    histories: dict[str, list[float]] = {}
    disabled_until: dict[str, pd.Timestamp] = {}
    probe_active_until: dict[str, pd.Timestamp] = {}
    next_trade_id = 1

    def close_due(now: pd.Timestamp) -> None:
        nonlocal balance, peak, max_dd
        due = sorted(
            [item for item in active if pd.Timestamp(item["exit_time"]) <= now],
            key=lambda item: pd.Timestamp(item["exit_time"]),
        )
        for item in due:
            before = balance
            peak_before = peak
            dd_before = _drawdown_percent(peak_before, before)
            pnl = float(item["risk_dollars"]) * float(item["r_multiple"])
            balance += pnl
            histories.setdefault(str(item["engine"]), []).append(float(item["r_multiple"]))
            if item.get("is_recovery_probe"):
                probe_active_until.pop(str(item["engine"]), None)
            peak = max(peak, balance)
            dd_after = _drawdown_percent(peak, balance)
            max_dd = max(max_dd, dd_after)

            ledger.append(
                {
                    "engine": str(item["engine"]),
                    "trade_id": int(item["trade_id"]),
                    "symbol": str(item["symbol"]),
                    "side": _side_label(int(item["side"])),
                    "entry_time": pd.Timestamp(item["entry_time"]).isoformat(),
                    "entry_price": "",
                    "exit_time": pd.Timestamp(item["exit_time"]).isoformat(),
                    "exit_price": "",
                    "stop_loss": "",
                    "take_profit": "",
                    "setup": str(item["setup"]),
                    "risk_percent": float(item["risk_percent"]),
                    "position_size": "",
                    "risk_dollars": float(item["risk_dollars"]),
                    "r_multiple": float(item["r_multiple"]),
                    "pnl": pnl,
                    "equity_before": before,
                    "equity_after": balance,
                    "peak_before": peak_before,
                    "peak_after": peak,
                    "drawdown_before": dd_before,
                    "drawdown_after": dd_after,
                    "guard_reason": str(item.get("guard_reason", "")),
                    "guard_multiplier": float(item.get("guard_multiplier", 1.0)),
                    "is_recovery_probe": bool(item.get("is_recovery_probe", False)),
                    "exit_reason": "SIMULATED_R_MULTIPLE_CLOSE",
                }
            )
            equity_rows.append(
                {
                    "time": pd.Timestamp(item["exit_time"]).isoformat(),
                    "event": "CLOSE",
                    "trade_id": int(item["trade_id"]),
                    "engine": str(item["engine"]),
                    "symbol": str(item["symbol"]),
                    "pnl": pnl,
                    "equity": balance,
                    "balance": balance,
                    "peak_equity": peak,
                    "drawdown_percent": dd_after,
                }
            )
            active.remove(item)

    for row in data.itertuples(index=False):
        entry_time = pd.Timestamp(row.entry_time)
        close_due(entry_time)
        engine = str(row.engine)
        decision = study._guard_decision(
            engine, histories, entry_time, disabled_until, probe_active_until, guard
        )
        if decision.multiplier <= 0:
            rejected.append({**row._asdict(), "reason": f"guard:{decision.reason}"})
            continue

        adjusted = row._asdict()
        adjusted["risk_percent"] = float(row.risk_percent) * decision.multiplier
        reason = study._position_reason(active, study._proxy(adjusted), config)
        if reason:
            rejected.append({**adjusted, "reason": reason})
            continue

        open_risk_before = sum(float(item["risk_percent"]) for item in active)
        risk_dollars = balance * float(adjusted["risk_percent"]) / 100.0
        item = {
            **adjusted,
            "trade_id": next_trade_id,
            "risk_dollars": risk_dollars,
            "guard_reason": decision.reason,
            "guard_multiplier": decision.multiplier,
            "is_recovery_probe": decision.is_probe,
            "equity_at_entry": balance,
            "open_risk_before_entry": open_risk_before,
            "open_risk_after_entry": open_risk_before + float(adjusted["risk_percent"]),
        }
        next_trade_id += 1
        active.append(item)
        accepted.append(item)
        if decision.is_probe:
            disabled_until.pop(engine, None)
            probe_active_until[engine] = pd.Timestamp(row.exit_time)
        stressed = balance - sum(float(position["risk_dollars"]) for position in active)
        stress_dd = max(stress_dd, _drawdown_percent(peak, stressed))

    close_due(pd.Timestamp.max.tz_localize("UTC"))

    accepted_frame = pd.DataFrame(accepted)
    rejected_frame = pd.DataFrame(rejected)
    ledger_frame = pd.DataFrame(ledger).sort_values(["exit_time", "trade_id"]).reset_index(drop=True)
    equity_frame = pd.DataFrame(equity_rows)

    if accepted_frame.empty:
        gross_income = gross_loss = 0.0
    else:
        pnl = accepted_frame["risk_dollars"] * accepted_frame["r_multiple"]
        gross_income = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())

    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "starting_balance": float(study.STARTING_BALANCE),
        "ending_balance": balance,
        "gross_income": gross_income,
        "gross_loss": gross_loss,
        "net_profit": balance - float(study.STARTING_BALANCE),
        "return_percent": (balance / float(study.STARTING_BALANCE) - 1) * 100,
        "average_monthly_profit": (balance - float(study.STARTING_BALANCE)) / max(1.0, (end - start).days / 30.4375),
        "trades": int(len(accepted_frame)),
        "profit_factor": gross_income / gross_loss if gross_loss else (math.inf if gross_income else 0.0),
        "max_drawdown_percent": max_dd,
        "stress_drawdown_percent": stress_dd,
        "rejections": rejected_frame["reason"].value_counts().to_dict() if not rejected_frame.empty else {},
    }
    return summary, accepted_frame, rejected_frame, ledger_frame, equity_frame


def main() -> None:
    prepared = {symbol: study._prepare(symbol) for symbol in study.ALL_SYMBOLS}
    runner_candidates = build_final_candidates(prepared)

    baseline, _ = targeted.baseopt.build_baseline_candidates(prepared)
    expected_candidates = targeted.filter_losers(baseline)

    runner_keys = _normalized_keys(runner_candidates)
    expected_keys = _normalized_keys(expected_candidates)
    missing = sorted(expected_keys - runner_keys)
    extra = sorted(runner_keys - expected_keys)
    if missing or extra:
        raise AssertionError(
            f"runner candidate parity failed: missing={len(missing)} extra={len(extra)}"
        )

    forbidden = {"GBPUSD_SWING_CORE", "GBPJPY_SWING_RETEST"}
    present_forbidden = forbidden & set(runner_candidates["engine"].astype(str))
    if present_forbidden:
        raise AssertionError(f"disabled engines present: {sorted(present_forbidden)}")

    common_end = min(prepared[s][1]["time"].max() for s in study.ALL_SYMBOLS)
    common_start = max(prepared[s][1]["time"].min() for s in study.ALL_SYMBOLS)

    original_guard = study._guard_decision
    study._guard_decision = targeted.targeted_guard_decision
    try:
        summary, accepted, rejected, ledger, equity = replay_with_ledger(
            runner_candidates, common_start, common_end, study.CAPACITY_CAPS
        )
    finally:
        study._guard_decision = original_guard

    net_profit = float(summary["net_profit"])
    if abs(net_profit - EXPECTED_NET_PROFIT) > PROFIT_TOLERANCE:
        raise AssertionError(
            f"V12 final profit drifted: expected {EXPECTED_NET_PROFIT}, got {net_profit}"
        )
    if int(summary["trades"]) != EXPECTED_TRADES:
        raise AssertionError(
            f"V12 final trade count drifted: expected {EXPECTED_TRADES}, got {summary['trades']}"
        )
    if abs(float(summary["profit_factor"]) - EXPECTED_PROFIT_FACTOR) > PF_TOLERANCE:
        raise AssertionError(
            f"V12 final PF drifted: expected about {EXPECTED_PROFIT_FACTOR}, got {summary['profit_factor']}"
        )
    if abs(float(summary["max_drawdown_percent"]) - EXPECTED_MAX_DD_PERCENT) > DD_TOLERANCE:
        raise AssertionError(
            f"V12 final max DD drifted: expected about {EXPECTED_MAX_DD_PERCENT}, got {summary['max_drawdown_percent']}"
        )

    result = {
        "status": "PASS",
        "profile": "V12_FINAL_3201_58",
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "candidate_count": int(len(runner_candidates)),
        "accepted_count": int(len(accepted)),
        "rejected_count": int(len(rejected)),
        "summary": summary,
        "missing_candidates": len(missing),
        "extra_candidates": len(extra),
        "outputs": {
            "ledger": str(OUT / "v12_final_trade_ledger.csv"),
            "equity_curve": str(OUT / "v12_final_equity_curve.csv"),
            "accepted": str(OUT / "v12_final_accepted.csv"),
            "rejected": str(OUT / "v12_final_rejected.csv"),
            "candidates": str(OUT / "v12_final_candidates.csv"),
            "summary": str(OUT / "v12_final_ledger_summary.json"),
        },
    }

    runner_candidates.to_csv(OUT / "v12_final_candidates.csv", index=False)
    accepted.to_csv(OUT / "v12_final_accepted.csv", index=False)
    rejected.to_csv(OUT / "v12_final_rejected.csv", index=False)
    ledger.to_csv(OUT / "v12_final_trade_ledger.csv", index=False)
    equity.to_csv(OUT / "v12_final_equity_curve.csv", index=False)
    (OUT / "v12_final_ledger_summary.json").write_text(
        json.dumps(result, indent=2, default=_json_safe), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, default=_json_safe))


if __name__ == "__main__":
    main()
