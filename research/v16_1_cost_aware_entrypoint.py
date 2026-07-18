"""Cost-accounting correction for the V16 five-symbol replacement replay.

The V15 generators expose both raw R and an execution reserve. Some inherited
core candidates already have that reserve deducted, while the newer H1/H4/D1
candidates carry it as metadata for later deduction. This wrapper normalizes
both representations, deducts every existing reserve exactly once, applies the
additional V16 live reserve, and recalculates the core walk-forward gate from
the resulting all-in R stream.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v16_live_cost_five_symbol_entrypoint as model  # noqa: E402


def all_in_cost_buffer(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    """Deduct declared historical and V16 reserves exactly once per candidate."""
    work = frame.copy()
    current_r = pd.to_numeric(work["r_multiple"], errors="coerce")
    raw_r = pd.to_numeric(work.get("raw_r_multiple", current_r), errors="coerce")
    declared_cost = pd.to_numeric(
        work.get("cost_r", work.get("selection_cost_r", 0.0)), errors="coerce"
    )
    if not isinstance(declared_cost, pd.Series):
        declared_cost = pd.Series(float(declared_cost or 0.0), index=work.index)
    declared_cost = declared_cost.fillna(0.0).clip(lower=0.0)

    already_deducted = (raw_r - current_r).clip(lower=0.0)
    unapplied_declared = (declared_cost - already_deducted).clip(lower=0.0)
    live_extra = work.apply(model.extra_cost_for_row, axis=1).astype(float)

    work["pre_v16_r_multiple"] = current_r
    work["declared_cost_r"] = declared_cost
    work["already_deducted_cost_r"] = already_deducted
    work["newly_deducted_declared_cost_r"] = unapplied_declared
    work["live_cost_buffer_r"] = live_extra
    work["r_multiple"] = current_r - unapplied_declared - live_extra
    work["cost_r"] = already_deducted + unapplied_declared + live_extra
    work["v16_cost_model"] = label
    return work


def cost_aware_baseline(
    core: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    baseline, evidence = model.v15.build_baseline_candidates(core)
    baseline = all_in_cost_buffer(
        baseline, "FXCM_BID_ASK_PLUS_ALL_DECLARED_AND_V16_LIVE_RESERVES"
    )

    gate_columns = (
        "gate_active",
        "gate_reason",
        "trailing_trades",
        "trailing_net_r",
        "trailing_profit_factor",
        "trailing_expectancy_r",
        "gate_score",
        "priority_score",
    )
    baseline = baseline.drop(columns=[column for column in gate_columns if column in baseline], errors="ignore")
    baseline = model.v15.v149.apply_walk_forward_gate(baseline)
    baseline["priority_score"] = pd.to_numeric(
        baseline.get("gate_score", 0.0), errors="coerce"
    ).fillna(0.0)
    baseline["requested_risk_percent"] = pd.to_numeric(
        baseline["risk_percent"], errors="coerce"
    )
    baseline["priority_class"] = 0
    return baseline, evidence


def main() -> None:
    model.apply_live_cost_buffer = all_in_cost_buffer
    model.prepare_baseline = cost_aware_baseline
    model.main()


if __name__ == "__main__":
    main()
