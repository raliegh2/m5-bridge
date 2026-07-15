"""Cross-window stabilized version of the five-symbol ICT research replay."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_all_symbol_ict import PROFILES, generate_candidates, performance, prepare_frames
from mt5_ai_bridge.v14_3_all_symbol_ict_admission import ADMISSION, apply_shadow_admission
from research.v13_expanded_assets_backtest import load_frame
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates, load_v12
from research.v14_3_five_symbol_ict_10y_backtest import (
    NEW_ICT_SYMBOLS,
    install_all_symbol_ict_profile,
    plot_results,
    run_case,
    write_report,
)

SEGMENT_GATE = {
    "minimum_trades_each_segment": 12,
    "minimum_net_r_each_segment": 0.0,
    "minimum_profit_factor_each_segment": 1.02,
}


def _frozen_profile(symbol: str):
    name = ADMISSION[symbol].profile_name
    return next(profile for profile in PROFILES[symbol] if profile.name == name)


def _segment_report(candidates: pd.DataFrame, start: pd.Timestamp, development_end: pd.Timestamp) -> dict[str, Any]:
    confirmation_start = start + (development_end - start) * 0.50
    early = candidates[candidates["entry_time"] < confirmation_start]
    confirmation = candidates[
        (candidates["entry_time"] >= confirmation_start)
        & (candidates["entry_time"] < development_end)
    ]
    validation = candidates[candidates["entry_time"] >= development_end]
    return {
        "early_development": performance(early),
        "confirmation": performance(confirmation),
        "development": performance(candidates[candidates["entry_time"] < development_end]),
        "validation": performance(validation),
        "all": performance(candidates),
        "confirmation_start": confirmation_start.isoformat(),
    }


def _passes_segments(stats: dict[str, Any]) -> bool:
    for key in ("early_development", "confirmation", "validation"):
        item = stats[key]
        if int(item["trades"]) < SEGMENT_GATE["minimum_trades_each_segment"]:
            return False
        if float(item["net_r"]) <= SEGMENT_GATE["minimum_net_r_each_segment"]:
            return False
        if float(item["profit_factor"] or 0.0) < SEGMENT_GATE["minimum_profit_factor_each_segment"]:
            return False
    return True


def build_stabilized_candidates(output: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    admitted: list[pd.DataFrame] = []
    reports: dict[str, Any] = {}
    folder = output / "new_ict_candidates"
    folder.mkdir(parents=True, exist_ok=True)

    for symbol in NEW_ICT_SYMBOLS:
        h1, _, _ = prepare_frames(
            load_frame(symbol, "h1"),
            load_frame(symbol, "h4"),
            load_frame(symbol, "d1"),
        )
        data_start = h1["time"].min()
        data_end = h1["time"].max()
        development_end = data_start + (data_end - data_start) * 0.65
        profile = _frozen_profile(symbol)
        raw = generate_candidates(symbol, h1, profile)
        candidates = apply_shadow_admission(symbol, raw)
        stats = _segment_report(candidates, data_start, development_end)
        passed = _passes_segments(stats)
        report = {
            "symbol": symbol,
            "engine": str(candidates["engine"].iloc[0]) if not candidates.empty else None,
            "setup": str(candidates["setup"].iloc[0]) if not candidates.empty else None,
            "selected": profile.name,
            "selected_profile": asdict(profile),
            "admission": asdict(ADMISSION[symbol]),
            "profiles": {profile.name: stats},
            "data_start": data_start.isoformat(),
            "data_end": data_end.isoformat(),
            "development_end": development_end.isoformat(),
            "segment_gate": SEGMENT_GATE,
            "validation_gate": SEGMENT_GATE,
            "validation_passed": passed,
            "raw_candidate_count": int(len(raw)),
            "candidate_count": int(len(candidates)),
        }
        reports[symbol] = report
        raw.to_csv(folder / f"{symbol.lower()}_raw_shadow_candidates.csv", index=False)
        candidates.to_csv(folder / f"{symbol.lower()}_admitted_shadow_candidates.csv", index=False)
        (folder / f"{symbol.lower()}_selection.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        if passed:
            admitted.append(candidates)

    combined = pd.concat(admitted, ignore_index=True).sort_values(["entry_time", "symbol", "engine"])
    combined = combined.drop_duplicates(["entry_time", "exit_time", "symbol", "engine", "side"])
    combined.to_csv(output / "admitted_new_ict_candidates.csv", index=False)
    return combined.reset_index(drop=True), reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Run cross-window stabilized five-symbol ICT replay")
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    install_all_symbol_ict_profile()
    v12 = apply_weak_symbol_profile(load_v12(args.v12_ledger))
    existing_ict = load_ict_candidates(args.ict_source)
    new_ict, selection = build_stabilized_candidates(args.out)

    latest = max(v12["exit_time"].max(), existing_ict["exit_time"].max(), new_ict["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    results = {
        "full_repository_history": run_case(
            "full_repository_history", v12, existing_ict, new_ict, args.out
        ),
        "exact_10_year_window": run_case(
            "exact_10_year_window",
            filter_window(v12, start, latest),
            filter_window(existing_ict, start, latest),
            filter_window(new_ict, start, latest),
            args.out,
        ),
    }
    payload = {"selection": selection, "results": results}
    (args.out / "all_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_report(results, selection, args.out)
    plot_results(results, args.out)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
