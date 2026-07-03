"""Targeted weak-engine optimization for the $3,028.98 V12 model.

Keeps the profitable broad AUDUSD engine fully protected and unchanged. Tests
post-hoc removal of the two full-sample losing sub-engines and applies adaptive
risk only to the thin-edge USDJPY and EURUSD retest components.

This is exploratory research because full-sample loser removal uses historical
outcomes. It must not be deployed without forward validation.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import v12_weak_engine_optimization as baseopt
import v12_plus_protected_assets_backtest as protected
import v12_plus_validated_assets_backtest as study

OUT = study.ROOT / "research" / "v12_targeted_weak_engine_output"
OUT.mkdir(parents=True, exist_ok=True)

FULL_SAMPLE_LOSERS = frozenset({"GBPUSD_SWING_CORE", "GBPJPY_SWING_RETEST"})
THIN_EDGE_ENGINES = frozenset({"USDJPY_SAFE_HAVEN_BREAKOUT", "EURUSD_SWING_RETEST"})
FULL_PROTECTED = frozenset(
    {
        study.PRECISION_ENGINE,
        "GBPUSD_SWING_RETEST",
        "EURUSD_SWING_CORE",
        "GBPJPY_SWING_CORE",
        "AUDUSD_TREND_PULLBACK",
    }
)
TARGETED_GUARD = study.GuardConfig(
    rolling=16,
    minimum=12,
    full_pf=1.08,
    full_net_r=0.0,
    reduced_pf=0.95,
    reduced_net_r=-1.0,
    reduced_multiplier=0.60,
    cooldown_days=45,
    probe_multiplier=0.35,
)
ORIGINAL_GUARD = study._guard_decision


def targeted_guard_decision(
    engine,
    history,
    now,
    disabled_until,
    probe_active_until,
    config,
):
    if engine in FULL_PROTECTED:
        return study.GuardDecision(1.0, "strong_or_audusd_protected")
    return ORIGINAL_GUARD(
        engine,
        history,
        now,
        disabled_until,
        probe_active_until,
        TARGETED_GUARD if engine in THIN_EDGE_ENGINES else config,
    )


def filter_losers(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[~frame["engine"].isin(FULL_SAMPLE_LOSERS)].copy().reset_index(drop=True)


def main() -> None:
    prepared = {symbol: study._prepare(symbol) for symbol in study.ALL_SYMBOLS}
    baseline, admission = baseopt.build_baseline_candidates(prepared)
    filtered = filter_losers(baseline)

    common_end = min(prepared[s][1]["time"].max() for s in study.ALL_SYMBOLS)
    common_start = max(prepared[s][1]["time"].min() for s in study.ALL_SYMBOLS)
    windows = {
        "max": common_start,
        "5y": max(common_start, common_end - pd.DateOffset(years=5)),
        "3y": max(common_start, common_end - pd.DateOffset(years=3)),
        "2y": max(common_start, common_end - pd.DateOffset(years=2)),
        "1y": max(common_start, common_end - pd.DateOffset(years=1)),
        "6m": max(common_start, common_end - pd.DateOffset(months=6)),
    }
    scenarios = {
        "baseline_3k_model": (baseline, protected.protected_guard_decision),
        "remove_both_losing_engines": (filtered, protected.protected_guard_decision),
        "remove_both_plus_targeted_guard": (filtered, targeted_guard_decision),
        "targeted_guard_only": (baseline, targeted_guard_decision),
    }

    results = {
        "status": "POST_HOC_EXPLORATORY_RESEARCH_ONLY",
        "starting_balance": study.STARTING_BALANCE,
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "portfolio_config": asdict(study.CAPACITY_CAPS),
        "targeted_guard": asdict(TARGETED_GUARD),
        "removed_engines": sorted(FULL_SAMPLE_LOSERS),
        "adaptive_engines": sorted(THIN_EDGE_ENGINES),
        "audusd_unchanged": True,
        "admission": admission,
        "scenarios": {},
    }
    summaries, symbols, engines = [], [], []
    for scenario, (candidates, guard) in scenarios.items():
        study._guard_decision = guard
        results["scenarios"][scenario] = {}
        candidates.to_csv(OUT / f"{scenario}_candidates.csv", index=False)
        for window, start in windows.items():
            summary, accepted, rejected = study._replay(
                candidates, start, common_end, study.CAPACITY_CAPS
            )
            results["scenarios"][scenario][window] = summary
            summaries.append({"scenario": scenario, "window": window, **summary})
            symbols.append(study._attribution(accepted, scenario, window, "symbol"))
            engines.append(study._attribution(accepted, scenario, window, "engine"))
            accepted.to_csv(OUT / f"accepted_{scenario}_{window}.csv", index=False)
            rejected.to_csv(OUT / f"rejected_{scenario}_{window}.csv", index=False)
    study._guard_decision = ORIGINAL_GUARD

    summary_frame = pd.DataFrame(summaries)
    symbol_frame = pd.concat(symbols, ignore_index=True)
    engine_frame = pd.concat(engines, ignore_index=True)
    summary_frame.to_csv(OUT / "scenario_summary.csv", index=False)
    symbol_frame.to_csv(OUT / "profit_by_symbol.csv", index=False)
    engine_frame.to_csv(OUT / "profit_by_engine.csv", index=False)
    (OUT / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        "# V12 Targeted Weak-Engine Optimization",
        "",
        "Status: **POST-HOC EXPLORATORY RESEARCH — DO NOT DEPLOY**",
        "",
        "| Scenario | Window | Trades | Net profit | Ending balance | PF | Max DD | Stress DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_frame.itertuples(index=False):
        lines.append(
            f"| {row.scenario} | {row.window} | {row.trades} | ${row.net_profit:.2f} | "
            f"${row.ending_balance:.2f} | {row.profit_factor:.3f} | "
            f"{row.max_drawdown_percent:.2f}% | {row.stress_drawdown_percent:.2f}% |"
        )
    (OUT / "V12_TARGETED_WEAK_ENGINE_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
