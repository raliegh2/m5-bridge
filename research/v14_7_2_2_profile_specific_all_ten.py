"""V14.7.2.2 profile-specific all-ten replay.

The earlier all-ten replay grouped GBPUSD wide-sweep profiles under one engine.
That allowed the weak gu_london_15 stream to dilute the validated gu_london_25
edge. This wrapper reconstructs the exact V14.6.1 incumbent candidates and
restricts the new GBPUSD ICT sleeve to gu_london_25 before retail-cost replay.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v14_7_2_frozen_all_ten as study  # noqa: E402
from research.v14_7_2_1_all_ten_replay import combined_raw_candidates  # noqa: E402

OUT = ROOT / "research" / "v14_7_2_2_profile_specific_output"
ORIGINAL_MATERIALIZE = study.materialize


def profile_specific_materialize(
    source: pd.DataFrame,
    symbol: str,
    mode: str,
    spec,
    risk: float,
    setup: str,
) -> pd.DataFrame:
    frame = ORIGINAL_MATERIALIZE(source, symbol, mode, spec, risk, setup)
    if (
        symbol == "GBPUSD"
        and mode == "ICT"
        and str(spec.engine) == "GBPUSD_ICT_WIDE_SWEEP"
    ):
        frame = frame[frame["profile"].astype(str) == "gu_london_25"].copy()
        if frame.empty:
            raise RuntimeError("Validated GBPUSD gu_london_25 profile is missing")
        frame["setup"] = "v14_7_2_2_gbpusd_ict_gu_london_25"
    return frame.reset_index(drop=True)


def main() -> None:
    study.OUT = OUT
    study.raw_candidates = combined_raw_candidates
    study.materialize = profile_specific_materialize
    study.main()

    result_path = OUT / "v14_7_2_results.json"
    payload = json.loads(result_path.read_text())
    gbpusd = payload["selections"]["GBPUSD"]["ICT"]
    gbpusd["profile"] = "gu_london_25"
    gbpusd["profile_isolated_before_replay"] = True
    result_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
