"""V14.5.2 robust profit-filter backtest.

Compares V14.5.1 with a strictly lower-or-equal-risk V14.5.2 overlay. The
candidate stream, promoted engines, observation tier, open-risk limits,
drawdown governor, entries, stops and targets remain unchanged.

V14.5.2 demotes three pre-entry time buckets to 0.025% observation risk:
* EURUSD_SWING_CORE on Monday UTC;
* EURUSD_SWING_CORE at 16:00 UTC;
* GBPJPY_SWING_CORE on Tuesday UTC.

The buckets were negative in both a development period ending 2018-12-31 and
a validation period from 2019-01-01 through 2022-03-05. This is an R-multiple
replay, not a tick simulation.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile
from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor
from mt5_ai_bridge.v14_3_satellite_symbol_profile import (
    apply_satellite_v12_risk,
    filter_satellite_ict,
    install_satellite_ict_risk,
)
from mt5_ai_bridge.v14_5_cost_robust_profile import (
    PROMOTED_V12_ENGINES,
    V14_5_OBSERVATION_RISK_PERCENT,
    V14_5_PROMOTED_RISK_PERCENT,
)
from mt5_ai_bridge.v14_5_2_profit_filter_profile import (
    v14_5_2_filter_reason,
    v14_5_2_risk_percent,
)
from research.v14_3_five_symbol_ict_10y_backtest import (
    build_new_ict_candidates,
    install_all_symbol_ict_profile,
)
from research.v14_3_production_improved_backtest import (
    filter_window,
    load_ict_candidates,
    load_v12,
)
from research.v14_3_satellite_symbol_profit_backtest import combine_ict, run_replay
from research.v14_3_satellite_symbol_profit_backtest_v2 import load_raw_shadow_candidates

ROOT = Path(__file__).resolve().parents[1]
V12_LEDGER = ROOT / "research" / "v12_final_ledger_output" / "v12_final_trade_ledger.csv"
ICT_SOURCE = (
    ROOT
    / "research"
    / "v14_3_true_combined_v12_ict_output"
    / "true_combined_closed_trades.csv"
)
OUT = ROOT / "research" / "v14_5_2_profit_filter_output"
GEN = OUT / "generated_candidates"

COST_SCENARIOS = {
    "zero_cost": {"V12": 0.0, "ICT": 0.0},
    "demo_cost": {"V12": 0.02, "ICT": 0.075},
    "retail_cost": {"V12": 0.03, "ICT": 0.13},
}
MODELS = ("v14_5_1", "v14_5_2")
EXPECTED_V14_5_1_EXACT = {
    "zero_cost": 4_618.459842363702,
    "demo_cost": 3_557.302443855495,
    "retail_cost": 2_942.0764504438857,
}
DEVELOPMENT_END = pd.Timestamp("2018-12-31 23:59:59", tz="UTC")
VALIDATION_START = pd.Timestamp("2019-01-01 00:00:00", tz="UTC")
VALIDATION_END = pd.Timestamp("2022-03-05 23:59:59", tz="UTC")


def prepare_models() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    GEN.mkdir(parents=True, exist_ok=True)
    install_all_symbol_ict_profile()
    baseline_v12 = apply_weak_symbol_profile(load_v12(V12_LEDGER))
    existing_ict = load_ict_candidates(ICT_SOURCE)
    _, selection = build_new_ict_candidates(GEN)
    raw_new_ict = load_raw_shadow_candidates(GEN)

    install_satellite_ict_risk()
    live_v12 = apply_satellite_v12_risk(baseline_v12)
    live_new_ict = filter_satellite_ict(raw_new_ict)
    live_ict = combine_ict(existing_ict, live_new_ict)

    v14_5_1 = live_v12.copy()
    v14_5_1["risk_percent"] = v14_5_1["engine"].map(
        lambda engine: (
            V14_5_PROMOTED_RISK_PERCENT
            if str(engine) in PROMOTED_V12_ENGINES
            else V14_5_OBSERVATION_RISK_PERCENT
        )
    )
    v14_5_1["profit_filter_reason"] = None

    v14_5_2 = live_v12.copy()
    v14_5_2["profit_filter_reason"] = v14_5_2.apply(
        lambda row: v14_5_2_filter_reason(str(row["engine"]), row["entry_time"]),
        axis=1,
    )
    v14_5_2["risk_percent"] = v14_5_2.apply(
        lambda row: v14_5_2_risk_percent(
            str(row["engine"]), "V12", row["entry_time"]
        ),
        axis=1,
    )

    ict = live_ict.copy()
    ict["risk_percent"] = V14_5_OBSERVATION_RISK_PERCENT
    return v14_5_1, v14_5_2, ict, selection


def apply_costs(frame: pd.DataFrame, cost_r: float) -> pd.DataFrame:
    output = frame.copy()
    output["raw_r_multiple"] = output["r_multiple"].astype(float)
    output["cost_r"] = float(cost_r)
    output["r_multiple"] = output["raw_r_multiple"] - float(cost_r)
    return output


def observation_registry(ict: pd.DataFrame) -> dict[tuple[str, str], float]:
    return {
        (str(row.symbol).upper(), str(row.setup)): V14_5_OBSERVATION_RISK_PERCENT
        for row in ict[["symbol", "setup"]].drop_duplicates().itertuples(index=False)
    }


def set_ict_registry(values: dict[tuple[str, str], float]) -> None:
    profit_profile.SETUP_RISK_PERCENT.clear()
    profit_profile.SETUP_RISK_PERCENT.update(values)


def summary_row(
    window: str,
    cost_name: str,
    model: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "window": window,
        "cost_scenario": cost_name,
        "model": model,
        "starting_balance": 5_000.0,
        "ending_balance": summary["ending_balance"],
        "net_profit": summary["net_profit"],
        "return_percent": summary["return_percent"],
        "profit_factor": summary["profit_factor"],
        "max_closed_drawdown_percent": summary["max_closed_drawdown_percent"],
        "stress_drawdown_percent": summary["stress_drawdown_percent"],
        "closed_trades": summary["closed_trades"],
        "skipped_trades": summary["skipped_ict_trades"],
    }


def annual_rows(
    trades: pd.DataFrame,
    window: str,
    cost_name: str,
    model: str,
) -> list[dict[str, Any]]:
    if trades.empty:
        return []
    frame = trades.copy()
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["year"] = frame["exit_time"].dt.year
    output: list[dict[str, Any]] = []
    for year, group in frame.groupby("year", sort=True):
        ordered = group.sort_values(["exit_time", "trade_id"])
        output.append(
            {
                "window": window,
                "cost_scenario": cost_name,
                "model": model,
                "year": int(year),
                "trades": int(len(group)),
                "net_profit": float(group["pnl"].sum()),
                "ending_equity": float(ordered.iloc[-1]["equity_after"]),
            }
        )
    return output


def ratio_stats(values: pd.Series) -> dict[str, Any]:
    series = values.astype(float)
    gross_profit = float(series[series > 0].sum())
    gross_loss = float(-series[series < 0].sum())
    return {
        "trades": int(len(series)),
        "sum_r": round(float(series.sum()), 6),
        "expectancy_r": round(float(series.mean()), 6) if len(series) else None,
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else None,
    }


def filter_evidence(v12: pd.DataFrame) -> dict[str, Any]:
    frame = v12.copy()
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["demo_net_r"] = frame["r_multiple"].astype(float) - COST_SCENARIOS["demo_cost"]["V12"]
    frame["filter_reason"] = frame.apply(
        lambda row: v14_5_2_filter_reason(str(row["engine"]), row["entry_time"]),
        axis=1,
    )
    output: dict[str, Any] = {}
    for reason, group in frame[frame["filter_reason"].notna()].groupby("filter_reason"):
        development = group[group["entry_time"] <= DEVELOPMENT_END]
        validation = group[
            (group["entry_time"] >= VALIDATION_START)
            & (group["entry_time"] <= VALIDATION_END)
        ]
        output[str(reason)] = {
            "development_demo_cost": ratio_stats(development["demo_net_r"]),
            "validation_demo_cost": ratio_stats(validation["demo_net_r"]),
            "all_demo_cost": ratio_stats(group["demo_net_r"]),
        }
    return output


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(payload: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    exact = [row for row in rows if row["window"] == "exact_10_year"]
    lines = [
        "# V14.5.2 Profit-Filter Backtest",
        "",
        f"**Exact ten-year period:** {payload['windows']['exact_10_year']['start'][:10]} to {payload['windows']['exact_10_year']['end'][:10]}",
        "**Starting balance:** $5,000.00",
        "",
        "V14.5.2 retains every V14.5.1 engine and protection. It only demotes three robustly weak, pre-entry UTC time buckets from 0.75% to the existing 0.025% observation tier.",
        "",
        "## Exact ten-year results",
        "",
        "| Cost | Model | Net profit | Ending balance | PF | Max closed DD | Stress DD |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for cost_name in COST_SCENARIOS:
        for model in MODELS:
            row = next(
                item
                for item in exact
                if item["cost_scenario"] == cost_name and item["model"] == model
            )
            label = "V14.5.1" if model == "v14_5_1" else "V14.5.2"
            lines.append(
                f"| {cost_name} | {label} | ${row['net_profit']:,.2f} | ${row['ending_balance']:,.2f} | "
                f"{row['profit_factor']:.4f} | {row['max_closed_drawdown_percent']:.4f}% | "
                f"{row['stress_drawdown_percent']:.4f}% |"
            )

    lines += [
        "",
        "## Split validation at demo costs",
        "",
        "| Window | Model | Net profit | PF | Max closed DD | Stress DD |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for window in ("development", "validation"):
        for model in MODELS:
            row = next(
                item
                for item in rows
                if item["window"] == window
                and item["cost_scenario"] == "demo_cost"
                and item["model"] == model
            )
            label = "V14.5.1" if model == "v14_5_1" else "V14.5.2"
            lines.append(
                f"| {window} | {label} | ${row['net_profit']:,.2f} | {row['profit_factor']:.4f} | "
                f"{row['max_closed_drawdown_percent']:.4f}% | {row['stress_drawdown_percent']:.4f}% |"
            )

    lines += [
        "",
        "## Preserved controls",
        "",
        "- Promoted risk remains capped at 0.75%; no trade receives more risk than V14.5.1.",
        "- ICT and all demoted V12 engines remain at 0.025% observation risk.",
        "- The 3.25% combined open-risk cap and the 7.5/8.5/9.0/9.6 drawdown governor are unchanged.",
        "- Entry logic, stop losses, take profits and position exits are unchanged.",
        "- Filtered trades are observed rather than deleted, preserving data for the live expectancy tracker.",
        "",
        "## Limitations",
        "",
        "This is an R-multiple replay with fixed cost assumptions, not tick-level broker simulation. Live spread, slippage, swap, gaps and V14.4 broker-time guards can change results. Historical results do not guarantee future profitability.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_results(rows: list[dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt

    demo = [
        row
        for row in rows
        if row["window"] == "exact_10_year"
        and row["cost_scenario"] == "demo_cost"
    ]
    labels = ["V14.5.1" if row["model"] == "v14_5_1" else "V14.5.2" for row in demo]
    profits = [float(row["net_profit"]) for row in demo]
    figure = plt.figure(figsize=(8, 5))
    bars = plt.bar(labels, profits)
    plt.axhline(0, linewidth=1)
    plt.ylabel("Net profit ($)")
    plt.title("Exact ten-year demo-cost net profit")
    for bar, value in zip(bars, profits):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            f"${value:,.0f}",
            ha="center",
            va="bottom" if value >= 0 else "top",
        )
    plt.tight_layout()
    figure.savefig(OUT / "exact_10_year_demo_profit.png", dpi=170)
    plt.close(figure)

    drawdowns = [float(row["max_closed_drawdown_percent"]) for row in demo]
    figure = plt.figure(figsize=(8, 5))
    bars = plt.bar(labels, drawdowns)
    plt.ylabel("Maximum closed drawdown (%)")
    plt.title("Exact ten-year demo-cost drawdown")
    for bar, value in zip(bars, drawdowns):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            f"{value:.2f}%",
            ha="center",
            va="bottom",
        )
    plt.tight_layout()
    figure.savefig(OUT / "exact_10_year_demo_drawdown.png", dpi=170)
    plt.close(figure)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    v14_5_1, v14_5_2, ict, selection = prepare_models()
    latest = max(v14_5_1["exit_time"].max(), ict["exit_time"].max())
    earliest = min(v14_5_1["entry_time"].min(), ict["entry_time"].min())
    ten_start = latest - pd.DateOffset(years=10)
    windows = {
        "full_history": (None, latest),
        "exact_10_year": (ten_start, latest),
        "development": (ten_start, min(DEVELOPMENT_END, latest)),
        "validation": (VALIDATION_START, min(VALIDATION_END, latest)),
    }
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
    original_registry = dict(profit_profile.SETUP_RISK_PERCENT)
    registry = observation_registry(ict)

    results: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    yearly: list[dict[str, Any]] = []
    sources = {"v14_5_1": v14_5_1, "v14_5_2": v14_5_2}

    for window, (start, end) in windows.items():
        for cost_name, costs in COST_SCENARIOS.items():
            for model, v12 in sources.items():
                set_ict_registry(registry)
                v12_case = apply_costs(v12, costs["V12"])
                ict_case = apply_costs(ict, costs["ICT"])
                if start is not None:
                    v12_case = filter_window(v12_case, start, end)
                    ict_case = filter_window(ict_case, start, end)
                summary, trades, skipped, events = run_replay(
                    v12_case, ict_case, governor
                )
                key = f"{window}/{model}/{cost_name}"
                results[key] = {
                    "summary": summary,
                    "governor_interventions": int(len(events)),
                }
                rows.append(summary_row(window, cost_name, model, summary))
                yearly.extend(annual_rows(trades, window, cost_name, model))
                folder = OUT / "ledgers" / window / cost_name
                folder.mkdir(parents=True, exist_ok=True)
                trades.to_csv(folder / f"{model}_trades.csv", index=False)
                skipped.to_csv(folder / f"{model}_skipped.csv", index=False)
                events.to_csv(folder / f"{model}_governor.csv", index=False)

    set_ict_registry(original_registry)

    for cost_name, expected in EXPECTED_V14_5_1_EXACT.items():
        actual = results[f"exact_10_year/v14_5_1/{cost_name}"]["summary"]["net_profit"]
        if abs(actual - expected) > 0.05:
            raise RuntimeError(
                f"V14.5.1 benchmark mismatch for {cost_name}: expected {expected}, got {actual}"
            )

    payload = {
        "generated_at": datetime.now().isoformat(),
        "models": {
            "v14_5_1": {
                "promoted_engines": sorted(PROMOTED_V12_ENGINES),
                "promoted_risk_percent": V14_5_PROMOTED_RISK_PERCENT,
                "observation_risk_percent": V14_5_OBSERVATION_RISK_PERCENT,
            },
            "v14_5_2": {
                "same_engines_and_limits": True,
                "filters": [
                    "EURUSD_SWING_CORE Monday UTC -> observation",
                    "EURUSD_SWING_CORE 16UTC -> observation",
                    "GBPJPY_SWING_CORE Tuesday UTC -> observation",
                ],
            },
        },
        "cost_scenarios_r": COST_SCENARIOS,
        "windows": {
            name: {
                "start": (start if start is not None else earliest).isoformat(),
                "end": end.isoformat(),
            }
            for name, (start, end) in windows.items()
        },
        "filter_evidence": filter_evidence(v14_5_1),
        "filter_counts": {
            str(key): int(value)
            for key, value in v14_5_2["profit_filter_reason"].value_counts(dropna=True).items()
        },
        "source_selection": selection,
        "results": results,
    }
    (OUT / "v14_5_2_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_csv(
        OUT / "comparison_summary.csv",
        rows,
        [
            "window",
            "cost_scenario",
            "model",
            "starting_balance",
            "ending_balance",
            "net_profit",
            "return_percent",
            "profit_factor",
            "max_closed_drawdown_percent",
            "stress_drawdown_percent",
            "closed_trades",
            "skipped_trades",
        ],
    )
    write_csv(
        OUT / "yearly_profit_equity.csv",
        yearly,
        [
            "window",
            "cost_scenario",
            "model",
            "year",
            "trades",
            "net_profit",
            "ending_equity",
        ],
    )
    write_report(payload, rows)
    plot_results(rows)

    print(
        json.dumps(
            {
                "exact_ten_year_window": payload["windows"]["exact_10_year"],
                "filter_counts": payload["filter_counts"],
                "filter_evidence": payload["filter_evidence"],
                "exact_ten_year_results": {
                    key: value["summary"]
                    for key, value in results.items()
                    if key.startswith("exact_10_year/")
                },
                "development_demo": {
                    key: value["summary"]
                    for key, value in results.items()
                    if key.startswith("development/") and key.endswith("/demo_cost")
                },
                "validation_demo": {
                    key: value["summary"]
                    for key, value in results.items()
                    if key.startswith("validation/") and key.endswith("/demo_cost")
                },
                "output": str(OUT),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
