"""Chronological, cost-stressed exit search for GBPUSD Breakout V2.

Candidate selection uses only 2016-2021. The 2022-2026 window is evaluated
once after the development winner is frozen. A candidate is promoted only if
it beats the original model on holdout net profit without materially worsening
profit factor or drawdown.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict
from pathlib import Path

from run_gbpusd_breakout_v2_proxy import ProxyParams, load_h4, prepare, run


HIGHER_COST = {
    "spread_floor_pips": 1.5,
    "slippage_pips": 0.6,
}


def _metrics(h4, prepared, params, start, end):
    return run(
        h4,
        params=params,
        prepared=prepared,
        entry_start=start,
        entry_end=end,
        **HIGHER_COST,
    )[2]


def _candidate_grid():
    for target_r, trail_start_r, trail_atr, max_hold in itertools.product(
        (2.0, 2.25, 2.5),
        (1.0, 1.25),
        (2.25, 2.5, 2.75),
        (90, 120),
    ):
        yield ProxyParams(
            target_r=target_r,
            trail_start_r=trail_start_r,
            trail_atr=trail_atr,
            max_hold_h4_bars=max_hold,
        )


def optimize(path: Path) -> dict:
    h4 = load_h4(path)
    prepared = prepare(h4)
    baseline = ProxyParams()

    folds = (
        ("2016-01-01", "2019-01-01"),
        ("2019-01-01", "2022-01-01"),
    )
    rows = []
    for params in _candidate_grid():
        fold_metrics = [
            _metrics(h4, prepared, params, start, end) for start, end in folds
        ]
        if any(m["trades"] < 20 or m["net_profit"] <= 0 for m in fold_metrics):
            continue
        full_dev = _metrics(h4, prepared, params, "2016-01-01", "2022-01-01")
        rows.append({
            "params": params,
            "folds": fold_metrics,
            "development": full_dev,
            "robust_score": min(m["net_profit"] for m in fold_metrics),
        })

    if not rows:
        raise RuntimeError("No exit candidate passed the development gates")

    rows.sort(
        key=lambda row: (
            row["robust_score"],
            row["development"]["net_profit"],
            row["development"]["profit_factor"],
        ),
        reverse=True,
    )
    winner = rows[0]
    baseline_holdout = _metrics(
        h4, prepared, baseline, "2022-01-01", "2027-01-01"
    )
    winner_holdout = _metrics(
        h4, prepared, winner["params"], "2022-01-01", "2027-01-01"
    )
    baseline_full = _metrics(
        h4, prepared, baseline, "2016-01-01", "2027-01-01"
    )
    winner_full = _metrics(
        h4, prepared, winner["params"], "2016-01-01", "2027-01-01"
    )

    promoted = bool(
        winner_holdout["net_profit"] > baseline_holdout["net_profit"]
        and winner_holdout["profit_factor"] >= baseline_holdout["profit_factor"]
        and winner_holdout["max_drawdown"] <= baseline_holdout["max_drawdown"] * 1.10
        and winner_full["net_profit"] > baseline_full["net_profit"]
    )
    return {
        "cost_model": HIGHER_COST,
        "selection_rule": "maximize weakest 3-year development-fold net profit",
        "candidate_count": len(rows),
        "baseline_params": asdict(baseline),
        "winner_params": asdict(winner["params"]),
        "winner_development": winner["development"],
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
