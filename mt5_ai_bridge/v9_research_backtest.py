"""Strategy Engine V9 research replay.

Applies an hour-quality gate to GBPUSD_SATELLITE_V2 candidates and replays the
shared V8 portfolio risk reservation rules. This is a synchronized candidate
ledger replay, not a tick-level OHLC simulation.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

ALLOWED_GBPUSD_SATELLITE_HOURS_UTC = frozenset({7, 10, 11, 12, 14, 15, 16})
BLOCKED_GBPUSD_SATELLITE_HOURS_UTC = frozenset({8, 9, 13, 17})
INITIAL_BALANCE = 5_000.0
MAX_POSITIONS = 3
MAX_OPEN_RISK_PERCENT = 0.75
GBP_ALIGNED_CAP_PERCENT = 0.75
GBP_MIXED_CAP_PERCENT = 0.50


@dataclass(frozen=True)
class Metrics:
    starting_balance: float
    ending_balance: float
    net_profit: float
    return_percent: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    average_trade: float
    realized_drawdown_percent: float
    open_risk_stress_drawdown_percent: float
    maximum_win: float
    maximum_loss: float


def load_candidates(accepted_path: str | Path, rejected_path: str | Path) -> pd.DataFrame:
    accepted = pd.read_csv(accepted_path)
    rejected = pd.read_csv(rejected_path)
    for frame in (accepted, rejected):
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True, errors="raise")
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="raise")

    accepted = accepted.copy()
    accepted["source"] = "accepted_v8"
    accepted["source_priority"] = 0
    accepted["original_accepted_index"] = range(len(accepted))

    rejected = rejected.copy()
    rejected["source"] = "rejected_v8"
    rejected["source_priority"] = 1
    rejected["original_accepted_index"] = pd.NA

    candidates = pd.concat([accepted, rejected], ignore_index=True, sort=False)
    candidates = candidates.sort_values(
        ["entry_time", "source_priority", "original_accepted_index", "id"],
        na_position="last",
    ).reset_index(drop=True)
    candidates["candidate_order"] = range(len(candidates))
    return candidates


def v8_filter(_: pd.Series) -> bool:
    return True


def v9_filter(row: pd.Series) -> bool:
    if row["engine"] != "GBPUSD_SATELLITE_V2":
        return True
    return int(row["entry_time"].hour) in ALLOWED_GBPUSD_SATELLITE_HOURS_UTC


def _gbp_side(symbol: str, side: int) -> int:
    return int(side) if symbol.upper().startswith("GBP") else 0


def replay(
    candidates: pd.DataFrame,
    strategy_filter: Callable[[pd.Series], bool],
    initial_balance: float = INITIAL_BALANCE,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict], list[dict]]:
    events: list[tuple[pd.Timestamp, int, int, int, str]] = []
    for index, row in candidates.iterrows():
        order = int(row["candidate_order"])
        events.append((row["entry_time"], 1, order, index, "entry"))
        exit_priority = 2 if row["exit_time"] == row["entry_time"] else 0
        events.append((row["exit_time"], exit_priority, order, index, "exit"))
    events.sort(key=lambda event: (event[0], event[1], event[2]))

    balance = float(initial_balance)
    open_positions: dict[int, dict] = {}
    accepted: list[dict] = []
    rejected: list[dict] = []
    realized_curve = [{"time": None, "equity": balance}]
    stress_curve = [{"time": None, "equity": balance}]

    for event_time, _priority, _order, index, event_type in events:
        row = candidates.loc[index]

        if event_type == "exit":
            position = open_positions.pop(index, None)
            if position is not None:
                balance += position["pnl"]
                position["exit_balance"] = balance
                accepted.append(position)
            realized_curve.append({"time": event_time.isoformat(), "equity": balance})
            stress_curve.append({
                "time": event_time.isoformat(),
                "equity": balance - sum(p["risk_dollars"] for p in open_positions.values()),
            })
            continue

        if not strategy_filter(row):
            rejected.append({
                "reason": "strategy_hour_filter",
                **_candidate_identity(row),
            })
            realized_curve.append({"time": event_time.isoformat(), "equity": balance})
            stress_curve.append({
                "time": event_time.isoformat(),
                "equity": balance - sum(p["risk_dollars"] for p in open_positions.values()),
            })
            continue

        risk_percent = float(row["risk_percent"])
        risk_dollars = balance * risk_percent / 100.0
        open_risk_dollars = sum(p["risk_dollars"] for p in open_positions.values())
        rejection_reason: str | None = None

        if len(open_positions) >= MAX_POSITIONS:
            rejection_reason = "max_positions"
        elif open_risk_dollars + risk_dollars > balance * MAX_OPEN_RISK_PERCENT / 100.0 + 1e-9:
            rejection_reason = "max_open_risk"

        if rejection_reason is None and str(row["symbol"]).upper().startswith("GBP"):
            gbp_positions = [
                p for p in open_positions.values()
                if str(p["symbol"]).upper().startswith("GBP")
            ]
            gbp_risk_dollars = sum(p["risk_dollars"] for p in gbp_positions)
            sides = {_gbp_side(p["symbol"], p["side"]) for p in gbp_positions}
            sides.add(_gbp_side(row["symbol"], int(row["side"])))
            mixed_direction = len({side for side in sides if side}) > 1
            cap_percent = GBP_MIXED_CAP_PERCENT if mixed_direction else GBP_ALIGNED_CAP_PERCENT
            if gbp_risk_dollars + risk_dollars > balance * cap_percent / 100.0 + 1e-9:
                rejection_reason = "gbp_currency_risk_cap"

        if rejection_reason is not None:
            rejected.append({"reason": rejection_reason, **_candidate_identity(row)})
        else:
            r_multiple = float(row["r_multiple"])
            position = {
                **_candidate_identity(row),
                "risk_dollars": risk_dollars,
                "r_multiple": r_multiple,
                "pnl": risk_dollars * r_multiple,
                "entry_balance": balance,
            }
            open_positions[index] = position

        realized_curve.append({"time": event_time.isoformat(), "equity": balance})
        stress_curve.append({
            "time": event_time.isoformat(),
            "equity": balance - sum(p["risk_dollars"] for p in open_positions.values()),
        })

    if open_positions:
        raise RuntimeError("Replay ended with open positions")
    return pd.DataFrame(accepted), pd.DataFrame(rejected), realized_curve, stress_curve


def _candidate_identity(row: pd.Series) -> dict:
    return {
        "symbol": str(row["symbol"]),
        "engine": str(row["engine"]),
        "setup": str(row["setup"]),
        "side": int(row["side"]),
        "entry_time": row["entry_time"].isoformat(),
        "exit_time": row["exit_time"].isoformat(),
        "risk_percent": float(row["risk_percent"]),
        "source": str(row["source"]),
    }


def _maximum_drawdown_percent(curve: list[dict]) -> float:
    values = [float(point["equity"]) for point in curve]
    peak = values[0]
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak * 100.0)
    return maximum


def calculate_metrics(
    trades: pd.DataFrame,
    realized_curve: list[dict],
    stress_curve: list[dict],
    initial_balance: float = INITIAL_BALANCE,
) -> Metrics:
    pnl = trades["pnl"] if len(trades) else pd.Series(dtype=float)
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0)
    ending_balance = initial_balance + float(pnl.sum())
    return Metrics(
        starting_balance=initial_balance,
        ending_balance=ending_balance,
        net_profit=ending_balance - initial_balance,
        return_percent=(ending_balance - initial_balance) / initial_balance * 100.0,
        trades=int(len(trades)),
        wins=int((pnl > 0).sum()),
        losses=int((pnl <= 0).sum()),
        win_rate=float((pnl > 0).mean()) if len(pnl) else 0.0,
        profit_factor=profit_factor,
        average_trade=float(pnl.mean()) if len(pnl) else 0.0,
        realized_drawdown_percent=_maximum_drawdown_percent(realized_curve),
        open_risk_stress_drawdown_percent=_maximum_drawdown_percent(stress_curve),
        maximum_win=float(pnl.max()) if len(pnl) else 0.0,
        maximum_loss=float(pnl.min()) if len(pnl) else 0.0,
    )


def engine_summary(trades: pd.DataFrame) -> list[dict]:
    result: list[dict] = []
    for engine, group in trades.groupby("engine", sort=True):
        pnl = group["pnl"]
        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = float(-pnl[pnl < 0].sum())
        result.append({
            "engine": str(engine),
            "trades": int(len(group)),
            "net_profit": float(pnl.sum()),
            "profit_factor": gross_profit / gross_loss if gross_loss else float("inf"),
            "win_rate": float((pnl > 0).mean()),
            "average_trade": float(pnl.mean()),
        })
    return result


def run_period(candidates: pd.DataFrame, start: str | None, end: str | None, filter_fn):
    period = candidates.copy()
    if start:
        period = period[period["entry_time"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        period = period[period["entry_time"] < pd.Timestamp(end, tz="UTC")]
    period = period.reset_index(drop=True)
    period["candidate_order"] = range(len(period))
    trades, rejected, realized, stress = replay(period, filter_fn)
    return {
        "metrics": asdict(calculate_metrics(trades, realized, stress)),
        "rejected": int(len(rejected)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("accepted")
    parser.add_argument("rejected")
    parser.add_argument("--out", default="v9_research_output")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    candidates = load_candidates(args.accepted, args.rejected)

    v8_trades, v8_rejected, v8_realized, v8_stress = replay(candidates, v8_filter)
    v9_trades, v9_rejected, v9_realized, v9_stress = replay(candidates, v9_filter)
    v8_metrics = calculate_metrics(v8_trades, v8_realized, v8_stress)
    v9_metrics = calculate_metrics(v9_trades, v9_realized, v9_stress)

    payload = {
        "methodology": (
            "Synchronized candidate-ledger replay. Exact R outcomes are applied to "
            "risk dollars reserved at entry; existing open risk remains fixed in dollars."
        ),
        "strategy": {
            "name": "GBPUSD_SATELLITE_V3_HOUR_GATE",
            "allowed_hours_utc": sorted(ALLOWED_GBPUSD_SATELLITE_HOURS_UTC),
            "blocked_hours_utc": sorted(BLOCKED_GBPUSD_SATELLITE_HOURS_UTC),
            "other_engines": "unchanged",
        },
        "v8_control": asdict(v8_metrics),
        "v9_candidate": asdict(v9_metrics),
        "improvement": {
            "net_profit_dollars": v9_metrics.net_profit - v8_metrics.net_profit,
            "net_profit_percent": (
                (v9_metrics.net_profit / v8_metrics.net_profit - 1.0) * 100.0
                if v8_metrics.net_profit else 0.0
            ),
            "profit_factor_change": v9_metrics.profit_factor - v8_metrics.profit_factor,
            "realized_drawdown_change_percent_points": (
                v9_metrics.realized_drawdown_percent - v8_metrics.realized_drawdown_percent
            ),
            "stress_drawdown_change_percent_points": (
                v9_metrics.open_risk_stress_drawdown_percent
                - v8_metrics.open_risk_stress_drawdown_percent
            ),
        },
        "v9_engine_summary": engine_summary(v9_trades),
        "v9_rejection_reasons": {
            str(key): int(value)
            for key, value in v9_rejected["reason"].value_counts().items()
        },
        "period_tests": {
            "development_before_2026": {
                "v8": run_period(candidates, None, "2026-01-01", v8_filter),
                "v9": run_period(candidates, None, "2026-01-01", v9_filter),
            },
            "validation_2026": {
                "v8": run_period(candidates, "2026-01-01", None, v8_filter),
                "v9": run_period(candidates, "2026-01-01", None, v9_filter),
            },
        },
        "limitations": [
            "The hour gate was derived from the same one-year ledger, so the full-sample result is in-sample.",
            "The 2026 split is a robustness check, not a fully untouched test because the gate was selected after inspecting the full ledger.",
            "This is not a tick-level OHLC replay and cannot model signal changes, spread changes, or intrabar fill differences.",
        ],
    }

    (out / "strategy_engine_v9_results.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    v9_trades.to_csv(out / "strategy_engine_v9_accepted_trades.csv", index=False)
    v9_rejected.to_csv(out / "strategy_engine_v9_rejected_candidates.csv", index=False)
    pd.DataFrame(v8_realized).to_csv(out / "v8_realized_equity.csv", index=False)
    pd.DataFrame(v9_realized).to_csv(out / "v9_realized_equity.csv", index=False)
    print(json.dumps(payload, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
