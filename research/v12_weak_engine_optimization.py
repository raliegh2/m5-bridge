"""Optimize weak engines inside the profitable $3k V12 five-symbol model.

The $3,028.98 expanded-capacity model remains the immutable baseline. This
research runner changes only engines that were weak or unstable in the prior
portfolio attribution:

* GBPUSD_SWING_CORE and GBPJPY_SWING_RETEST are admitted only if both the
  development and confirmation segments are profitable with PF >= 1.0.
* USDJPY_SAFE_HAVEN_BREAKOUT, EURUSD_SWING_RETEST and AUDUSD_TREND_PULLBACK
  receive a repaired rolling risk guard instead of permanent protected status.
* Strong engines retain their original frozen risk and protected status.

The policy is selected on the first 70% of common history (50% development,
20% confirmation). The final 30% is untouched validation. No live orders are
sent and no MT5 connection is made.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import v12_audusd_quality_upgrade as quality
import v12_plus_protected_assets_backtest as protected
import v12_plus_validated_assets_backtest as study

OUT = study.ROOT / "research" / "v12_weak_engine_optimization_output"
OUT.mkdir(parents=True, exist_ok=True)

STRONG_PROTECTED_ENGINES = frozenset(
    {
        study.PRECISION_ENGINE,
        "GBPUSD_SWING_RETEST",
        "EURUSD_SWING_CORE",
        "GBPJPY_SWING_CORE",
    }
)
CANDIDATE_LOSER_ENGINES = frozenset(
    {"GBPUSD_SWING_CORE", "GBPJPY_SWING_RETEST"}
)
MARGINAL_ENGINES = frozenset(
    {
        "AUDUSD_TREND_PULLBACK",
        "USDJPY_SAFE_HAVEN_BREAKOUT",
        "EURUSD_SWING_RETEST",
    }
)

OPTIMIZED_GUARD = study.GuardConfig(
    rolling=16,
    minimum=12,
    full_pf=1.10,
    full_net_r=0.0,
    reduced_pf=0.95,
    reduced_net_r=-1.0,
    reduced_multiplier=0.50,
    cooldown_days=45,
    probe_multiplier=0.35,
)


def build_baseline_candidates(prepared: dict) -> tuple[pd.DataFrame, dict]:
    legacy = quality.build_legacy(prepared)
    aud_params, audusd, aud_report = study._select_audusd(prepared["AUDUSD"][1])
    usdjpy = study._usdjpy_candidates(prepared["USDJPY"][1])
    usdjpy_report = study._validation_report(usdjpy, prepared["USDJPY"][1])
    if not aud_report["passed"]:
        audusd = audusd.iloc[0:0].copy()
    if not usdjpy_report["passed"]:
        usdjpy = usdjpy.iloc[0:0].copy()
    candidates = quality.merge_frames(legacy, audusd, usdjpy)
    return candidates, {
        "audusd_params": asdict(aud_params),
        "audusd_admission": aud_report,
        "usdjpy_admission": usdjpy_report,
    }


def engine_segment_report(
    candidates: pd.DataFrame,
    common_start: pd.Timestamp,
    common_end: pd.Timestamp,
) -> dict:
    span = common_end - common_start
    development_end = common_start + span * 0.50
    confirmation_end = common_start + span * 0.70
    report = {
        "development_end": development_end.isoformat(),
        "confirmation_end": confirmation_end.isoformat(),
        "engines": {},
    }
    for engine, frame in candidates.groupby("engine"):
        development = study._stats(frame[frame["entry_time"] < development_end])
        confirmation = study._stats(
            frame[
                (frame["entry_time"] >= development_end)
                & (frame["entry_time"] < confirmation_end)
            ]
        )
        holdout = study._stats(frame[frame["entry_time"] >= confirmation_end])
        report["engines"][str(engine)] = {
            "development": development,
            "confirmation": confirmation,
            "untouched_holdout": holdout,
        }
    return report


def loser_policy(report: dict) -> dict[str, str]:
    policy = {}
    for engine in CANDIDATE_LOSER_ENGINES:
        stats = report["engines"].get(engine, {})
        development = stats.get("development", {})
        confirmation = stats.get("confirmation", {})
        robust = (
            development.get("trades", 0) >= 15
            and confirmation.get("trades", 0) >= 5
            and development.get("net_r", 0.0) > 0
            and confirmation.get("net_r", 0.0) > 0
            and development.get("profit_factor", 0.0) >= 1.0
            and confirmation.get("profit_factor", 0.0) >= 1.0
        )
        policy[engine] = "keep" if robust else "disable"
    return policy


def filter_disabled(candidates: pd.DataFrame, policy: dict[str, str]) -> pd.DataFrame:
    disabled = {engine for engine, action in policy.items() if action == "disable"}
    return candidates[~candidates["engine"].isin(disabled)].copy().reset_index(drop=True)


def optimized_guard_decision(
    engine,
    history,
    now,
    disabled_until,
    probe_active_until,
    config,
):
    if engine in STRONG_PROTECTED_ENGINES:
        return study.GuardDecision(1.0, "strong_engine_protected")
    return study._guard_decision(
        engine,
        history,
        now,
        disabled_until,
        probe_active_until,
        OPTIMIZED_GUARD if engine in MARGINAL_ENGINES else config,
    )


def attribution(frame: pd.DataFrame, scenario: str, window: str, column: str) -> pd.DataFrame:
    return study._attribution(frame, scenario, window, column)


def main() -> None:
    prepared = {symbol: study._prepare(symbol) for symbol in study.ALL_SYMBOLS}
    baseline_candidates, admission = build_baseline_candidates(prepared)

    common_end = min(
        prepared[symbol][1]["time"].max() for symbol in study.ALL_SYMBOLS
    )
    common_start = max(
        prepared[symbol][1]["time"].min() for symbol in study.ALL_SYMBOLS
    )
    report = engine_segment_report(baseline_candidates, common_start, common_end)
    removal_policy = loser_policy(report)
    loser_filtered = filter_disabled(baseline_candidates, removal_policy)

    windows = {
        "max": common_start,
        "5y": max(common_start, common_end - pd.DateOffset(years=5)),
        "3y": max(common_start, common_end - pd.DateOffset(years=3)),
        "2y": max(common_start, common_end - pd.DateOffset(years=2)),
        "1y": max(common_start, common_end - pd.DateOffset(years=1)),
        "6m": max(common_start, common_end - pd.DateOffset(months=6)),
    }
    scenarios = {
        "baseline_3k_model": {
            "candidates": baseline_candidates,
            "guard": protected.protected_guard_decision,
        },
        "remove_confirmed_losers": {
            "candidates": loser_filtered,
            "guard": protected.protected_guard_decision,
        },
        "adaptive_marginal_engines": {
            "candidates": baseline_candidates,
            "guard": optimized_guard_decision,
        },
        "optimized_combined": {
            "candidates": loser_filtered,
            "guard": optimized_guard_decision,
        },
    }

    results = {
        "status": "RESEARCH_ONLY_DO_NOT_DEPLOY",
        "starting_balance": study.STARTING_BALANCE,
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "portfolio_config": asdict(study.CAPACITY_CAPS),
        "optimized_guard": asdict(OPTIMIZED_GUARD),
        "admission": admission,
        "segment_report": report,
        "loser_policy": removal_policy,
        "scenarios": {},
    }
    summary_rows = []
    symbol_rows = []
    engine_rows = []

    original_guard = study._guard_decision
    for scenario, spec in scenarios.items():
        study._guard_decision = spec["guard"]
        results["scenarios"][scenario] = {}
        spec["candidates"].to_csv(OUT / f"{scenario}_candidates.csv", index=False)
        for window, start in windows.items():
            summary, accepted, rejected = study._replay(
                spec["candidates"], start, common_end, study.CAPACITY_CAPS
            )
            results["scenarios"][scenario][window] = summary
            summary_rows.append({"scenario": scenario, "window": window, **summary})
            symbol_rows.append(attribution(accepted, scenario, window, "symbol"))
            engine_rows.append(attribution(accepted, scenario, window, "engine"))
            accepted.to_csv(OUT / f"accepted_{scenario}_{window}.csv", index=False)
            rejected.to_csv(OUT / f"rejected_{scenario}_{window}.csv", index=False)
    study._guard_decision = original_guard

    summary_frame = pd.DataFrame(summary_rows)
    symbol_frame = pd.concat(symbol_rows, ignore_index=True)
    engine_frame = pd.concat(engine_rows, ignore_index=True)
    summary_frame.to_csv(OUT / "scenario_summary.csv", index=False)
    symbol_frame.to_csv(OUT / "profit_by_symbol.csv", index=False)
    engine_frame.to_csv(OUT / "profit_by_engine.csv", index=False)
    (OUT / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        "# V12 Weak-Engine Optimization",
        "",
        "Status: **RESEARCH ONLY — DO NOT DEPLOY**",
        "",
        f"Loser policy: `{json.dumps(removal_policy, sort_keys=True)}`",
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
    (OUT / "V12_WEAK_ENGINE_OPTIMIZATION_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
