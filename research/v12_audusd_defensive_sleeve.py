"""Conservative AUDUSD defensive sleeve for the V12 five-symbol portfolio.

This is an exploratory follow-up to the nested quality search. It tests the
small, historically stable AUDUSD subset that closes at 08:00 UTC on Monday or
Thursday. The sleeve is tested at 0.15% and 0.20% risk and compared with the
broad AUDUSD engine, the nested quality engine, and a no-AUDUSD control.

The rule is explicitly post-hoc and must remain research-only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import v12_audusd_quality_upgrade as quality
import v12_plus_protected_assets_backtest as protected
import v12_plus_validated_assets_backtest as study

OUT = study.ROOT / "research" / "v12_audusd_defensive_output"
OUT.mkdir(parents=True, exist_ok=True)


def defensive_candidates(frame: pd.DataFrame, risk_percent: float) -> pd.DataFrame:
    selected = frame[
        (frame["signal_hour"] == 8)
        & (frame["signal_weekday"].isin((0, 3)))
    ].copy()
    selected["risk_percent"] = float(risk_percent)
    selected["setup"] = f"AUDUSD_DEFENSIVE_08UTC_MON_THU_{risk_percent:.2f}PCT"
    return selected.reset_index(drop=True)


def segment_report(frame: pd.DataFrame, h4: pd.DataFrame) -> dict:
    split = h4["time"].min() + (h4["time"].max() - h4["time"].min()) * 0.70
    return {
        "split": split.isoformat(),
        "development": study._stats(frame[frame["entry_time"] < split]),
        "validation": study._stats(frame[frame["entry_time"] >= split]),
    }


def merge_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    usable = [frame for frame in frames if not frame.empty]
    return pd.concat(usable, ignore_index=True).sort_values(
        ["entry_time", "engine", "setup"]
    ).reset_index(drop=True)


def main() -> None:
    study._guard_decision = protected.protected_guard_decision
    prepared = {symbol: study._prepare(symbol) for symbol in study.ALL_SYMBOLS}
    legacy = quality.build_legacy(prepared)

    broad_params, broad_audusd, broad_report = study._select_audusd(
        prepared["AUDUSD"][1]
    )
    rich_audusd = quality.feature_candidates(prepared["AUDUSD"][1], broad_params)
    nested_rule, nested_audusd, nested_report = quality.select_quality_rule(
        rich_audusd, prepared["AUDUSD"][1]
    )
    if not nested_report["passed"]:
        nested_audusd = nested_audusd.iloc[0:0].copy()
    nested_audusd["risk_percent"] = 0.15
    nested_audusd["setup"] = "AUDUSD_NESTED_QUALITY_0.15PCT"

    defensive_015 = defensive_candidates(rich_audusd, 0.15)
    defensive_020 = defensive_candidates(rich_audusd, 0.20)
    defensive_report = segment_report(defensive_015, prepared["AUDUSD"][1])

    usdjpy = study._usdjpy_candidates(prepared["USDJPY"][1])
    usdjpy_report = study._validation_report(usdjpy, prepared["USDJPY"][1])
    if not usdjpy_report["passed"]:
        usdjpy = usdjpy.iloc[0:0].copy()

    common_end = min(
        prepared[symbol][1]["time"].max() for symbol in study.ALL_SYMBOLS
    )
    common_start = max(
        prepared[symbol][1]["time"].min() for symbol in study.ALL_SYMBOLS
    )
    windows = {
        "max": common_start,
        "5y": max(common_start, common_end - pd.DateOffset(years=5)),
        "3y": max(common_start, common_end - pd.DateOffset(years=3)),
        "2y": max(common_start, common_end - pd.DateOffset(years=2)),
        "1y": max(common_start, common_end - pd.DateOffset(years=1)),
        "6m": max(common_start, common_end - pd.DateOffset(months=6)),
    }
    scenarios = {
        "broad_audusd_025": merge_frames(legacy, broad_audusd, usdjpy),
        "nested_quality_015": merge_frames(legacy, nested_audusd, usdjpy),
        "defensive_sleeve_015": merge_frames(legacy, defensive_015, usdjpy),
        "defensive_sleeve_020": merge_frames(legacy, defensive_020, usdjpy),
        "no_audusd_control": merge_frames(legacy, usdjpy),
    }

    results = {
        "status": "POST_HOC_EXPLORATORY_RESEARCH_ONLY",
        "starting_balance": study.STARTING_BALANCE,
        "common_start": common_start.isoformat(),
        "common_end": common_end.isoformat(),
        "broad_report": broad_report,
        "nested_rule": nested_rule,
        "nested_report": nested_report,
        "defensive_rule": {
            "signal_hour": 8,
            "weekdays": [0, 3],
            "description": "08:00 UTC Monday/Thursday completed-H4 pullbacks",
        },
        "defensive_report": defensive_report,
        "usdjpy_report": usdjpy_report,
        "scenarios": {},
    }
    summary_rows = []
    symbol_rows = []
    engine_rows = []
    for scenario, candidates in scenarios.items():
        results["scenarios"][scenario] = {}
        candidates.to_csv(OUT / f"{scenario}_candidates.csv", index=False)
        for window, start in windows.items():
            summary, accepted, rejected = study._replay(
                candidates, start, common_end, study.CAPACITY_CAPS
            )
            results["scenarios"][scenario][window] = summary
            summary_rows.append({"scenario": scenario, "window": window, **summary})
            symbol_rows.append(study._attribution(accepted, scenario, window, "symbol"))
            engine_rows.append(study._attribution(accepted, scenario, window, "engine"))
            accepted.to_csv(OUT / f"accepted_{scenario}_{window}.csv", index=False)
            rejected.to_csv(OUT / f"rejected_{scenario}_{window}.csv", index=False)

    summary = pd.DataFrame(summary_rows)
    by_symbol = pd.concat(symbol_rows, ignore_index=True)
    by_engine = pd.concat(engine_rows, ignore_index=True)
    summary.to_csv(OUT / "scenario_summary.csv", index=False)
    by_symbol.to_csv(OUT / "profit_by_symbol.csv", index=False)
    by_engine.to_csv(OUT / "profit_by_engine.csv", index=False)
    defensive_015.to_csv(OUT / "audusd_defensive_candidates.csv", index=False)
    (OUT / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        "# V12 AUDUSD Defensive Sleeve",
        "",
        "Status: **POST-HOC EXPLORATORY RESEARCH — DO NOT DEPLOY**",
        "",
        f"Development: `{defensive_report['development']}`",
        f"Validation: `{defensive_report['validation']}`",
        "",
        "| Scenario | Window | Trades | Net profit | Ending balance | PF | Max DD | Stress DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.scenario} | {row.window} | {row.trades} | ${row.net_profit:.2f} | "
            f"${row.ending_balance:.2f} | {row.profit_factor:.3f} | "
            f"{row.max_drawdown_percent:.2f}% | {row.stress_drawdown_percent:.2f}% |"
        )
    (OUT / "V12_AUDUSD_DEFENSIVE_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
