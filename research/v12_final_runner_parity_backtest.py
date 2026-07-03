"""Parity backtest for ``v12_final_runner.py``.

Rebuilds public historical frames, passes them through the runner's candidate
builder, and replays the exact targeted portfolio policy. The script fails if
candidate parity or the final historical result changes unexpectedly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "research"))

import v12_plus_validated_assets_backtest as study
import v12_targeted_weak_engine_optimization as targeted
from v12_final_runner import build_final_candidates

OUT = ROOT / "research" / "v12_final_runner_parity_output"
OUT.mkdir(parents=True, exist_ok=True)
EXPECTED_NET_PROFIT = 3201.58
TOLERANCE = 0.05


def normalized_keys(frame: pd.DataFrame) -> set[tuple]:
    if frame.empty:
        return set()
    return {
        (
            str(row.symbol), str(row.engine), str(row.setup), int(row.side),
            pd.Timestamp(row.entry_time).isoformat(), float(row.risk_percent),
        )
        for row in frame.itertuples(index=False)
    }


def main() -> None:
    prepared = {symbol: study._prepare(symbol) for symbol in study.ALL_SYMBOLS}
    runner_candidates = build_final_candidates(prepared)

    baseline, _ = targeted.baseopt.build_baseline_candidates(prepared)
    expected_candidates = targeted.filter_losers(baseline)

    runner_keys = normalized_keys(runner_candidates)
    expected_keys = normalized_keys(expected_candidates)
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
        summary, accepted, rejected = study._replay(
            runner_candidates, common_start, common_end, study.CAPACITY_CAPS
        )
    finally:
        study._guard_decision = original_guard

    net_profit = float(summary["net_profit"])
    if abs(net_profit - EXPECTED_NET_PROFIT) > TOLERANCE:
        raise AssertionError(
            f"final runner profit drifted: expected {EXPECTED_NET_PROFIT}, got {net_profit}"
        )

    result = {
        "status": "PASS",
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "candidate_count": int(len(runner_candidates)),
        "accepted_count": int(len(accepted)),
        "rejected_count": int(len(rejected)),
        "summary": summary,
        "missing_candidates": len(missing),
        "extra_candidates": len(extra),
    }
    (OUT / "results.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    runner_candidates.to_csv(OUT / "runner_candidates.csv", index=False)
    accepted.to_csv(OUT / "accepted.csv", index=False)
    rejected.to_csv(OUT / "rejected.csv", index=False)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
