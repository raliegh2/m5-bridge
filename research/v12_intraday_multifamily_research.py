"""Three-way selection test for separate M15/M30 intraday families."""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from v12_intraday_m15_m30_backtest import IntradayParams, load_m5, run_backtest


FAMILIES = (
    "TREND_PULLBACK", "TREND_BREAKOUT_ONLY", "TREND_REENTRY_ONLY",
    "LONDON_ORB", "RANGE_REVERSION", "REGIME_ENSEMBLE",
)


def candidate_grid(family: str):
    adx_values = (16.0, 20.0, 24.0) if family != "RANGE_REVERSION" else (16.0, 20.0, 24.0)
    for adx_min in adx_values:
        for stop_atr in (1.0, 1.25, 1.5):
            for reward_risk in (1.0, 1.5, 2.0):
                for hold in (24, 36, 48):
                    yield IntradayParams(
                        family=family, adx_min=adx_min, stop_atr=stop_atr,
                        reward_risk=reward_risk, max_hold_m5_bars=hold,
                    )


def score(metrics: dict) -> float:
    if metrics.get("trades", 0) < 20:
        return -math.inf
    # Do not collapse every losing configuration to the same score. Keeping a
    # continuous return/drawdown score lets the report identify the least-bad
    # family honestly when no candidate is profitable.
    return (metrics.get("net_profit", 0.0)
            - 5.0 * metrics.get("max_drawdown_percent", 0.0))


def tune(frame: pd.DataFrame, family: str) -> tuple[IntradayParams, dict]:
    tested = []
    for params in candidate_grid(family):
        metrics, _ = run_backtest(frame, params)
        tested.append((score(metrics), params, metrics))
    _, params, metrics = max(tested, key=lambda item: item[0])
    return params, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("GBPUSD_M5.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("research/v12_intraday_multifamily_output"))
    args = parser.parse_args()
    data = load_m5(args.data)
    first = int(len(data) * 0.50)
    second = int(len(data) * 0.75)
    development = data.iloc[:first].copy()
    selection = data.iloc[first:second].copy()
    final_test = data.iloc[second:].copy()

    rows = []
    selected_by_family = {}
    for family in FAMILIES:
        params, development_metrics = tune(development, family)
        selection_metrics, _ = run_backtest(selection, params)
        selected_by_family[family] = params
        rows.append({
            "family": family, "params": asdict(params),
            "development": development_metrics,
            "selection": selection_metrics,
        })
    eligible = [row for row in rows
                if row["selection"].get("trades", 0) >= 15
                and (row["selection"].get("profit_factor") or 0) > 1.0
                and row["selection"].get("net_profit", 0) > 0]
    if eligible:
        chosen = max(eligible, key=lambda row: (
            row["selection"]["net_profit"]
            - 5 * row["selection"]["max_drawdown_percent"]))
        final_metrics, final_ledger = run_backtest(
            final_test, selected_by_family[chosen["family"]])
        promoted = (
            final_metrics.get("trades", 0) >= 15
            and (final_metrics.get("profit_factor") or 0) >= 1.10
            and final_metrics.get("net_profit", 0) > 0
            and final_metrics.get("max_drawdown_percent", 100) <= 5.0
        )
    else:
        chosen = None
        final_metrics = {"status": "NOT_RUN_NO_SELECTION_CANDIDATE"}
        final_ledger = pd.DataFrame()
        promoted = False

    payload = {
        "status": "PROMOTION_CANDIDATE" if promoted else "RESEARCH_ONLY",
        "target_average_weekly_profit": 50.0,
        "split": "50% parameter development / 25% family selection / 25% untouched final test",
        "family_results": rows,
        "chosen_family": chosen["family"] if chosen else None,
        "final_test": final_metrics,
        "promotion_gate": {
            "positive_final_net": True, "minimum_final_profit_factor": 1.10,
            "minimum_final_trades": 15, "maximum_final_drawdown_percent": 5.0,
        },
        "limitations": [
            "The entire dataset spans only 246 calendar days.",
            "A three-way split reduces overfitting but leaves short regime samples.",
            "Results are GBPUSD-only and include fixed, not tick-by-tick, costs.",
        ],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(payload, indent=2) + "\n",
                                            encoding="utf-8")
    if not final_ledger.empty:
        final_ledger.to_csv(args.out / "final_test_trades.csv", index=False)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
