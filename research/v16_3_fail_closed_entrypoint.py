"""Fail-closed V16 launcher with low-risk and baseline-only controls.

This launcher does not attempt to force the $20,000 target. It expands the risk
search below the original V16 floor and includes a true zero-allocation control
for the newly generated systems. The output therefore records the best safe
configuration even when the promotion gates fail.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v16_1_cost_aware_entrypoint as cost_patch  # noqa: E402
from research import v16_2_schema_safe_entrypoint as schema_patch  # noqa: E402
from research import v16_live_cost_five_symbol_entrypoint as model  # noqa: E402


BASE_MULTIPLIERS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.50)
NEW_MULTIPLIERS = (0.00, 0.10, 0.25, 0.50)


def conservative_scale_baseline(frame: pd.DataFrame, multiplier: float) -> pd.DataFrame:
    work = frame.copy()
    requested = pd.to_numeric(work["requested_risk_percent"], errors="coerce") * float(multiplier)
    work["requested_risk_percent"] = requested.clip(lower=0.01, upper=0.75)
    work["risk_percent"] = work["requested_risk_percent"]
    return work


def conservative_scale_new(frame: pd.DataFrame, multiplier: float) -> pd.DataFrame:
    if float(multiplier) <= 0.0:
        return frame.iloc[0:0].copy()
    work = frame.copy()
    requested = pd.to_numeric(work["requested_risk_percent"], errors="coerce") * float(multiplier)
    work["requested_risk_percent"] = requested.clip(lower=0.01, upper=0.50)
    work["risk_percent"] = work["requested_risk_percent"]
    return work


def main() -> None:
    model.BASE_RISK_MULTIPLIERS = BASE_MULTIPLIERS
    model.NEW_RISK_MULTIPLIERS = NEW_MULTIPLIERS
    model.apply_live_cost_buffer = cost_patch.all_in_cost_buffer
    model.prepare_baseline = cost_patch.cost_aware_baseline
    model.select_pre2016_profiles = cost_patch.select_only_qualified_new_profiles
    model.materialize_walk_forward = schema_patch.schema_safe_materialize
    model.scale_baseline = conservative_scale_baseline
    model.scale_new = conservative_scale_new
    model.main()


if __name__ == "__main__":
    main()
