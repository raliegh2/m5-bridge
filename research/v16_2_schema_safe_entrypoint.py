"""Schema-safe launcher for the V16 all-in-cost five-symbol replay."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v16_1_cost_aware_entrypoint as cost_patch  # noqa: E402
from research import v16_live_cost_five_symbol_entrypoint as model  # noqa: E402

_ORIGINAL_MATERIALIZE = model.materialize_walk_forward


def schema_safe_materialize(source: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    frame = _ORIGINAL_MATERIALIZE(source, selected)
    if frame.columns.duplicated().any():
        frame = frame.loc[:, ~frame.columns.duplicated(keep="last")].copy()
    if not frame.columns.is_unique:
        raise RuntimeError("V16 materialized candidate schema still contains duplicate columns")
    return frame


def main() -> None:
    model.apply_live_cost_buffer = cost_patch.all_in_cost_buffer
    model.prepare_baseline = cost_patch.cost_aware_baseline
    model.select_pre2016_profiles = cost_patch.select_only_qualified_new_profiles
    model.materialize_walk_forward = schema_safe_materialize
    model.main()


if __name__ == "__main__":
    main()
