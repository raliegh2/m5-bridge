"""Reproduce the V10 synchronized risk-allocation research result.

Input files are the V8 synchronized accepted and rejected candidate ledgers.
The V9 GBPUSD hour gate is applied before the V10 risk allocation. The script
is intentionally a ledger replay, not a tick/OHLC execution simulator.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.strategy_engine_v10 import V10_PROFILE

ALLOWED_GBPUSD_HOURS_UTC = frozenset({7, 10, 11, 12, 14, 15, 16})


def load_candidates(accepted: Path, rejected: Path) -> pd.DataFrame:
    accepted_frame = pd.read_csv(accepted)
    rejected_frame = pd.read_csv(rejected)
    for frame in (accepted_frame, rejected_frame):
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    accepted_frame["source_priority"] = 0
    rejected_frame["source_priority"] = 1
    candidates = pd.concat([accepted_frame, rejected_frame], ignore_index=True, sort=False)
    candidates = candidates.sort_values(
        ["entry_time", "source_priority", "id"], na_position="last"
    ).reset_index(drop=True)
    candidates["candidate_order"] = range(len(candidates))
    return candidates


def v9_hour_allowed(row: pd.Series) -> bool:
    if row["engine"] != "GBPUSD_SATELLITE_V2":
        return True
    return int(row["entry_time"].hour) in ALLOWED_GBPUSD_HOURS_UTC


def maximum_drawdown(values: list[float]) -> float:
    peak = values[0]
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        maximum = max(maximum, (peak - value) / peak * 100.0)
    return maximum


def replay(candidates: pd.DataFrame) -> dict:
    profile = V10_PROFILE
    balance = profile.initial_balance
    open_positions: dict[int, dict] = {}
    accepted: list[dict] = []
    rejected: list[dict] = []
    realized = [balance]
    stress = [balance]
    events: list[tuple] = []
    for index, row in candidates.iterrows():
        order = int(row["candidate_order"])
        events.append((row["entry_time"], 1, order, index, "entry"))
        exit_priority = 2 if row["exit_time"] == row["entry_time"] else 0
        events.append((row["exit_time"], exit_priority, order, index, "exit"))
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    for event_time, _priority, _order, index, event_type in events:
        row = candidates.loc[index]
        if event_type == "exit":
            position = open_positions.pop(index, None)
            if position is not None:
                balance += position["pnl"]
                accepted.append(position)
            realized.append(balance)
            stress.append(balance - sum(item["risk_dollars"] for item in open_positions.values()))
            continue

        if not v9_hour_allowed(row):
            rejected.append({"reason": "strategy_hour_filter", "id": row.get("id")})
            continue

        risk_percent = profile.risk_for(str(row["engine"]))
        risk_dollars = balance * risk_percent / 100.0
        open_risk = sum(item["risk_dollars"] for item in open_positions.values())
        reason = None
        if len(open_positions) >= profile.max_positions:
            reason = "max_positions"
        elif open_risk + risk_dollars > balance * profile.max_open_risk_percent / 100.0 + 1e-9:
            reason = "max_open_risk"
        if reason is None and str(row["symbol"]).upper().startswith("GBP"):
            gbp_positions = [
                item for item in open_positions.values()
                if str(item["symbol"]).upper().startswith("GBP")
            ]
            gbp_risk = sum(item["risk_dollars"] for item in gbp_positions)
            sides = {int(item["side"]) for item in gbp_positions}
            sides.add(int(row["side"]))
            mixed = len(sides) > 1
            cap = (
                profile.mixed_gbp_cap_percent
                if mixed else profile.aligned_gbp_cap_percent
            )
            if gbp_risk + risk_dollars > balance * cap / 100.0 + 1e-9:
                reason = "gbp_currency_risk_cap"
        if reason:
            rejected.append({"reason": reason, "id": row.get("id")})
            continue

        position = {
            "engine": str(row["engine"]),
            "symbol": str(row["symbol"]),
            "side": int(row["side"]),
            "entry_time": event_time.isoformat(),
            "exit_time": row["exit_time"].isoformat(),
            "risk_percent": risk_percent,
            "risk_dollars": risk_dollars,
            "r_multiple": float(row["r_multiple"]),
            "pnl": risk_dollars * float(row["r_multiple"]),
        }
        open_positions[index] = position
        realized.append(balance)
        stress.append(balance - sum(item["risk_dollars"] for item in open_positions.values()))

    pnl = pd.Series([item["pnl"] for item in accepted], dtype=float)
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    return {
        "profile": asdict(profile),
        "starting_balance": profile.initial_balance,
        "ending_balance": balance,
        "net_profit": balance - profile.initial_balance,
        "return_percent": (balance - profile.initial_balance) / profile.initial_balance * 100.0,
        "trades": len(accepted),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl <= 0).sum()),
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": gross_profit / gross_loss,
        "realized_drawdown_percent": maximum_drawdown(realized),
        "open_risk_stress_drawdown_percent": maximum_drawdown(stress),
        "rejection_reasons": pd.Series(
            [item["reason"] for item in rejected], dtype=str
        ).value_counts().to_dict(),
        "limitations": [
            "This is a synchronized candidate-ledger replay.",
            "Risk weights were selected using the same history and require forward validation.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("accepted", type=Path)
    parser.add_argument("rejected", type=Path)
    parser.add_argument("--output", type=Path, default=Path("v10_profitability_results.json"))
    args = parser.parse_args(argv)
    payload = replay(load_candidates(args.accepted, args.rejected))
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
