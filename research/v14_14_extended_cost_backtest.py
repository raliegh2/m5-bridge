"""V14.14 exact-ten-year extended transaction-cost replay.

Reuses the verified V14.13 chronology and accounting implementation while
replacing only the cost policy and adding severe/extreme cost scenarios.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

import research.v14_13_cost_regime_backtest as base
from mt5_ai_bridge.v14_13_cost_regime_profile import strict_retail_profile
from mt5_ai_bridge.v14_14_extended_cost_profile import (
    ExtendedCostRegimeConfig,
    extended_cost_regime_decision,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_14_extended_cost_output"

EXTENDED_COST_SCENARIOS = {
    "zero_cost": {"V12": 0.0, "ICT": 0.0},
    "demo_cost": {"V12": 0.02, "ICT": 0.075},
    "retail_cost": {"V12": 0.03, "ICT": 0.13},
    "stress_cost": {"V12": 0.05, "ICT": 0.18},
    "severe_cost": {"V12": 0.08, "ICT": 0.23},
    "extreme_cost": {"V12": 0.10, "ICT": 0.28},
}


def ratio_stats(values: pd.Series) -> dict[str, Any]:
    series = pd.to_numeric(values, errors="coerce").dropna()
    gross_profit = float(series[series > 0].sum())
    gross_loss = float(-series[series < 0].sum())
    return {
        "trades": int(len(series)),
        "net_r": float(series.sum()),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
    }


def extended_profile_evidence(ict: pd.DataFrame) -> dict[str, Any]:
    frame = ict.copy()
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["strict_profile"] = frame.apply(
        lambda row: strict_retail_profile(
            str(row["symbol"]),
            str(row["setup"]),
            str(row.get("side", "")),
            row["entry_time"],
        ),
        axis=1,
    )
    selected = frame[
        frame["strict_profile"]
        & frame["symbol"].isin(["GBPUSD", "GBPJPY"])
    ].copy()
    output: dict[str, Any] = {}
    for cost_name in ("retail_cost", "stress_cost", "severe_cost", "extreme_cost"):
        cost_r = float(EXTENDED_COST_SCENARIOS[cost_name]["ICT"])
        blocks: dict[str, Any] = {}
        for year in (2023, 2024, 2025, 2026):
            group = selected[selected["entry_time"].dt.year == year]
            blocks[str(year)] = ratio_stats(
                group["r_multiple"].astype(float) - cost_r
            )
        blocks["all"] = ratio_stats(
            selected["r_multiple"].astype(float) - cost_r
        )
        output[cost_name] = blocks
    return output


def main() -> None:
    base.OUT = OUT
    base.GEN = OUT / "generated_candidates"
    base.COST_SCENARIOS = EXTENDED_COST_SCENARIOS
    base.CostRegimeConfig = ExtendedCostRegimeConfig
    base.cost_regime_decision = extended_cost_regime_decision
    base.strict_profile_evidence = extended_profile_evidence
    base.main()


if __name__ == "__main__":
    main()
