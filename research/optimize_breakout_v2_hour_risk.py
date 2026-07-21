"""Cost-stressed chronological hour-risk allocation for Breakout V2."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from run_gbpusd_breakout_v2_proxy import ProxyParams, load_h4, prepare, run


HIGHER_COST = {"spread_floor_pips": 1.5, "slippage_pips": 0.6}
ALLOCATIONS = (
    (1.0, 1.0),
    (0.8, 1.2),
    (0.6, 1.4),
    (0.5, 1.5),
    (1.2, 0.8),
    (1.4, 0.6),
)


def _metrics(h4, prepared, params, start, end):
    return run(
        h4,
        params=params,
        prepared=prepared,
        entry_start=start,
        entry_end=end,
        **HIGHER_COST,
    )[2]


def optimize(path: Path) -> dict:
    h4 = load_h4(path)
    prepared = prepare(h4)
    baseline = ProxyParams()
    folds = (("2016-01-01", "2019-01-01"), ("2019-01-01", "2022-01-01"))
    candidates = []
    for risk_12, risk_16 in ALLOCATIONS:
        params = ProxyParams(
            risk_multiplier_12utc=risk_12,
            risk_multiplier_16utc=risk_16,
        )
        fold_metrics = [
            _metrics(h4, prepared, params, start, end) for start, end in folds
        ]
        if any(m["trades"] < 20 or m["net_profit"] <= 0 for m in fold_metrics):
            continue
        candidates.append({
            "params": params,
            "folds": fold_metrics,
            "robust_score": min(m["net_profit"] for m in fold_metrics),
        })
    candidates.sort(key=lambda c: c["robust_score"], reverse=True)
    winner = candidates[0]["params"]

    baseline_holdout = _metrics(h4, prepared, baseline, "2022-01-01", "2027-01-01")
    winner_holdout = _metrics(h4, prepared, winner, "2022-01-01", "2027-01-01")
    baseline_full = _metrics(h4, prepared, baseline, "2016-01-01", "2027-01-01")
    winner_full = _metrics(h4, prepared, winner, "2016-01-01", "2027-01-01")
    promoted = bool(
        winner_holdout["net_profit"] > baseline_holdout["net_profit"]
        and winner_holdout["profit_factor"] >= baseline_holdout["profit_factor"]
        and winner_holdout["max_drawdown"] <= baseline_holdout["max_drawdown"] * 1.10
        and winner_full["net_profit"] > baseline_full["net_profit"]
    )
    return {
        "cost_model": HIGHER_COST,
        "baseline_params": asdict(baseline),
        "winner_params": asdict(winner),
        "baseline_holdout": baseline_holdout,
        "winner_holdout": winner_holdout,
        "baseline_full": baseline_full,
        "winner_full": winner_full,
        "promoted": promoted,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--h4", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = optimize(args.h4)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
