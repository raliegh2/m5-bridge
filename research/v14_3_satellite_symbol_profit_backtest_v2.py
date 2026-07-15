"""Second-pass satellite replay including raw shadow candidates for all symbols."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor
from mt5_ai_bridge.v14_3_satellite_symbol_profile import (
    apply_satellite_v12_risk,
    filter_satellite_ict,
    install_satellite_ict_risk,
)
from research.v14_3_five_symbol_ict_10y_backtest import build_new_ict_candidates, install_all_symbol_ict_profile
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates, load_v12
from research.v14_3_satellite_symbol_profit_backtest import (
    combine_ict,
    plot_results,
    run_case,
    validation_windows,
    write_report,
)


def load_raw_shadow_candidates(output: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    mapping = {
        "EURUSD": "eurusd_shadow_candidates.csv",
        "AUDUSD": "audusd_shadow_candidates.csv",
        "USDJPY": "usdjpy_shadow_candidates.csv",
    }
    for symbol, filename in mapping.items():
        path = output / "new_ict_candidates" / filename
        frame = pd.read_csv(path)
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
        if frame.empty:
            raise RuntimeError(f"No raw shadow candidates generated for {symbol}")
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False).sort_values(["entry_time", "symbol", "engine"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run material satellite-symbol profit replay")
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    install_all_symbol_ict_profile()
    baseline_v12 = apply_weak_symbol_profile(load_v12(args.v12_ledger))
    existing_ict = load_ict_candidates(args.ict_source)
    admitted_new_ict, selection = build_new_ict_candidates(args.out)
    baseline_ict = combine_ict(existing_ict, admitted_new_ict)

    raw_new_ict = load_raw_shadow_candidates(args.out)
    install_satellite_ict_risk()
    enhanced_v12 = apply_satellite_v12_risk(baseline_v12)
    enhanced_new_ict = filter_satellite_ict(raw_new_ict)
    enhanced_new_ict.to_csv(args.out / "enhanced_new_ict_candidates.csv", index=False)
    enhanced_ict = combine_ict(existing_ict, enhanced_new_ict)
    validation = validation_windows(enhanced_new_ict)

    latest = max(
        baseline_v12["exit_time"].max(),
        baseline_ict["exit_time"].max(),
        enhanced_ict["exit_time"].max(),
    )
    start = latest - pd.DateOffset(years=10)
    governor = DrawdownGovernor(
        soft_start_percent=7.50,
        medium_start_percent=8.50,
        defensive_start_percent=9.00,
        hard_stop_percent=9.60,
        soft_multiplier=0.98,
        medium_multiplier=0.82,
        defensive_multiplier=0.50,
        minimum_risk_percent=0.025,
    )
    results = {
        "full_repository_history": run_case(
            "full_repository_history",
            baseline_v12,
            baseline_ict,
            enhanced_v12,
            enhanced_ict,
            governor,
            args.out,
        ),
        "exact_10_year_window": run_case(
            "exact_10_year_window",
            filter_window(baseline_v12, start, latest),
            filter_window(baseline_ict, start, latest),
            filter_window(enhanced_v12, start, latest),
            filter_window(enhanced_ict, start, latest),
            governor,
            args.out,
        ),
    }
    payload = {
        "governor": governor.__dict__,
        "source_selection": selection,
        "satellite_validation": validation,
        "results": results,
    }
    (args.out / "all_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_report(results, validation, args.out)
    plot_results(results, args.out)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
