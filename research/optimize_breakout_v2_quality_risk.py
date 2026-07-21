"""Chronological quality-tier risk validation for GBPUSD Breakout V2."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from run_gbpusd_breakout_v2_proxy import ProxyParams, load_h4, prepare, run


HIGHER_COST = {"spread_floor_pips": 1.5, "slippage_pips": 0.6}
PROFILES = (
    ProxyParams(),
    ProxyParams(quality_volume_min=1.0, quality_range_atr_min=1.0,
                quality_risk_multiplier=1.25, standard_risk_multiplier=0.75),
    ProxyParams(quality_volume_min=1.248, quality_range_atr_min=1.555,
                quality_risk_multiplier=1.25, standard_risk_multiplier=0.75),
    ProxyParams(quality_volume_min=1.248, quality_range_atr_min=1.555,
                quality_risk_multiplier=1.50, standard_risk_multiplier=0.75),
    ProxyParams(quality_volume_min=1.248, quality_range_atr_min=None,
                quality_risk_multiplier=1.25, standard_risk_multiplier=0.75),
)


def _metrics(h4, prepared, params, start, end):
    return run(h4, params=params, prepared=prepared, entry_start=start,
               entry_end=end, **HIGHER_COST)[2]


def optimize(path: Path) -> dict:
    h4 = load_h4(path)
    prepared = prepare(h4)
    baseline = ProxyParams()
    folds = (("2016-01-01", "2019-01-01"), ("2019-01-01", "2022-01-01"))
    candidates = []
    for params in PROFILES:
        fold_metrics = [_metrics(h4, prepared, params, a, b) for a, b in folds]
        if any(m["trades"] < 20 or m["net_profit"] <= 0 for m in fold_metrics):
            continue
        candidates.append({
            "params": params,
            "robust_score": min(m["net_profit"] for m in fold_metrics),
        })
    candidates.sort(key=lambda c: c["robust_score"], reverse=True)
    winner = candidates[0]["params"]
    baseline_holdout = _metrics(h4, prepared, baseline, "2022-01-01", "2027-01-01")
    winner_holdout = _metrics(h4, prepared, winner, "2022-01-01", "2027-01-01")
    baseline_full = _metrics(h4, prepared, baseline, "2016-01-01", "2027-01-01")
    winner_full = _metrics(h4, prepared, winner, "2016-01-01", "2027-01-01")
    # Profit-first promotion requested for this pass. Drawdown must remain
    # below the explicit 7% research ceiling and full-period PF cannot decline.
    promoted = bool(
        winner_holdout["net_profit"] > baseline_holdout["net_profit"]
        and winner_full["net_profit"] > baseline_full["net_profit"]
        and winner_full["profit_factor"] >= baseline_full["profit_factor"]
        and winner_full["max_drawdown"] <= 0.07
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
