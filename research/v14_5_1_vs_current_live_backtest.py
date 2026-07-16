"""Corrected V14.5.1 versus enhanced live V14.3 backtest.

The current live research-parity model is the enhanced V14.3 satellite
portfolio whose exact-ten-year zero-cost benchmark is $34,690.84 net from a
$5,000 start. This runner rebuilds that same V12 + ICT candidate set, verifies
the benchmark, and compares V14.5.1 over identical chronological windows.

Costs are expressed in R per trade. This is an R-multiple replay rather than a
tick simulation; broker-native spread variation, slippage, swap, gaps and live
V14.4 expectancy/staleness gates require forward-test observations.
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
ICT_SOURCE = ROOT / "research" / "v14_3_true_combined_v12_ict_output" / "true_combined_closed_trades.csv"
OUT_DIR = ROOT / "research" / "v14_5_1_vs_current_live_output"
GEN_DIR = OUT_DIR / "generated_candidates"
EXPECTED_LIVE_TEN_YEAR_NET = 34_690.840749742056

COST_SCENARIOS = {
    "zero_cost": {"V12": 0.0, "ICT": 0.0},
    "demo_cost": {"V12": 0.02, "ICT": 0.075},
    "retail_cost": {"V12": 0.03, "ICT": 0.13},
}
MODELS = ("current_live_v14_3", "v14_5_1")


def prepare_models() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Rebuild the exact enhanced-live inputs, then apply V14.5.1 allocation."""
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    install_all_symbol_ict_profile()

    baseline_v12 = apply_weak_symbol_profile(load_v12(V12_LEDGER))
    existing_ict = load_ict_candidates(ICT_SOURCE)
    _, selection = build_new_ict_candidates(GEN_DIR)
    raw_new_ict = load_raw_shadow_candidates(GEN_DIR)

    install_satellite_ict_risk()
    live_v12 = apply_satellite_v12_risk(baseline_v12)
    live_new_ict = filter_satellite_ict(raw_new_ict)
    live_ict = combine_ict(existing_ict, live_new_ict)

    v14_5_1_v12 = live_v12.copy()
    v14_5_1_v12["risk_percent"] = v14_5_1_v12["engine"].map(
        lambda engine: (
            V14_5_PROMOTED_RISK_PERCENT
            if str(engine) in PROMOTED_V12_ENGINES
            else V14_5_OBSERVATION_RISK_PERCENT
        )
    )
    v14_5_1_ict = live_ict.copy()
    v14_5_1_ict["risk_percent"] = V14_5_OBSERVATION_RISK_PERCENT
    return live_v12, live_ict, v14_5_1_v12, v14_5_1_ict, selection


def apply_costs(frame: pd.DataFrame, cost_r: float) -> pd.DataFrame:
    output = frame.copy()
    output["raw_r_multiple"] = output["r_multiple"].astype(float)
    output["cost_r"] = float(cost_r)
    output["r_multiple"] = output["raw_r_multiple"] - float(cost_r)
    return output


def set_ict_risk_registry(values: dict[tuple[str, str], float]) -> None:
    profit_profile.SETUP_RISK_PERCENT.clear()
    profit_profile.SETUP_RISK_PERCENT.update(values)


def v14_5_1_registry(live_ict: pd.DataFrame) -> dict[tuple[str, str], float]:
    return {
        (str(row.symbol).upper(), str(row.setup)): V14_5_OBSERVATION_RISK_PERCENT
        for row in live_ict[["symbol", "setup"]].drop_duplicates().itertuples(index=False)
    }


def annual_rows(trades: pd.DataFrame, model: str, cost_name: str, window: str) -> list[dict[str, Any]]:
    if trades.empty:
        return []
    frame = trades.copy()
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["year"] = frame["exit_time"].dt.year
    rows: list[dict[str, Any]] = []
    for year, group in frame.groupby("year", sort=True):
        rows.append({
            "window": window,
            "cost_scenario": cost_name,
            "model": model,
            "year": int(year),
            "trades": int(len(group)),
            "net_profit": float(group["pnl"].sum()),
            "ending_equity": float(group.sort_values(["exit_time", "trade_id"]).iloc[-1]["equity_after"]),
        })
    return rows


def summary_row(window: str, cost_name: str, model: str, summary: dict[str, Any]) -> dict[str, Any]:
    start = summary.get("starting_balance", profit_profile.PORTFOLIO_GUARD.starting_balance)
    end = summary["ending_balance"]
    return {
        "window": window,
        "cost_scenario": cost_name,
        "model": model,
        "starting_balance": start,
        "ending_balance": end,
        "net_profit": summary["net_profit"],
        "return_percent": summary["return_percent"],
        "profit_factor": summary["profit_factor"],
        "max_closed_drawdown_percent": summary["max_closed_drawdown_percent"],
        "stress_drawdown_percent": summary["stress_drawdown_percent"],
        "closed_trades": summary["closed_trades"],
        "skipped_trades": summary["skipped_ict_trades"],
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report(payload: dict[str, Any], rows: list[dict[str, Any]], yearly: list[dict[str, Any]]) -> None:
    start = payload["windows"]["exact_10_year"]["start"][:10]
    end = payload["windows"]["exact_10_year"]["end"][:10]
    lines = [
        "# Corrected V14.5.1 vs Enhanced Live V14.3 Backtest",
        "",
        f"**Exact comparison window:** {start} to {end}",
        "**Starting balance:** $5,000.00",
        "",
        "The current-live benchmark is the enhanced V14.3 satellite model, not the earlier combined-ledger proxy. The zero-cost replay is required to reproduce approximately $34,690.84 net profit before any comparison is accepted.",
        "",
        "## Exact ten-year comparison",
        "",
        "| Cost | Model | Trades | Net profit | Ending balance | Return | PF | Max closed DD | Stress DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cost in COST_SCENARIOS:
        for model in MODELS:
            row = next(x for x in rows if x["window"] == "exact_10_year" and x["cost_scenario"] == cost and x["model"] == model)
            label = "Enhanced live V14.3" if model == "current_live_v14_3" else "V14.5.1"
            lines.append(
                f"| {cost} | {label} | {row['closed_trades']} | ${row['net_profit']:,.2f} | "
                f"${row['ending_balance']:,.2f} | {row['return_percent']:.2f}% | {row['profit_factor']:.4f} | "
                f"{row['max_closed_drawdown_percent']:.4f}% | {row['stress_drawdown_percent']:.4f}% |"
            )

    lines += [
        "",
        "## Demo-cost annual profit",
        "",
        "| Year | Enhanced live V14.3 | V14.5.1 |",
        "|---:|---:|---:|",
    ]
    years = sorted({int(x["year"]) for x in yearly if x["window"] == "exact_10_year" and x["cost_scenario"] == "demo_cost"})
    for year in years:
        current = next((x for x in yearly if x["window"] == "exact_10_year" and x["cost_scenario"] == "demo_cost" and x["model"] == "current_live_v14_3" and int(x["year"]) == year), {"net_profit": 0.0})
        new = next((x for x in yearly if x["window"] == "exact_10_year" and x["cost_scenario"] == "demo_cost" and x["model"] == "v14_5_1" and int(x["year"]) == year), {"net_profit": 0.0})
        lines.append(f"| {year} | ${float(current['net_profit']):,.2f} | ${float(new['net_profit']):,.2f} |")

    lines += [
        "",
        "## Interpretation",
        "",
        "- Zero cost preserves the published historical benchmark but is not a broker-realistic profitability estimate.",
        "- Demo and retail scenarios subtract constant R costs from every trade before the same chronological replay, so costs also affect compounding, drawdown and loss-control state.",
        "- V14.5.1 intentionally puts all ICT and demoted swing streams at 0.025% observation risk; therefore it can sacrifice gross upside to improve cost robustness and drawdown.",
        "- Results remain research estimates and do not guarantee live profitability.",
    ]
    (OUT_DIR / "BACKTEST_COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot(rows: list[dict[str, Any]], yearly: list[dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt

    demo = [x for x in rows if x["window"] == "exact_10_year" and x["cost_scenario"] == "demo_cost"]
    fig = plt.figure(figsize=(8, 5))
    labels = ["Enhanced live V14.3" if x["model"] == "current_live_v14_3" else "V14.5.1" for x in demo]
    values = [float(x["net_profit"]) for x in demo]
    bars = plt.bar(labels, values)
    plt.axhline(0, linewidth=1)
    plt.ylabel("Net profit ($)")
    plt.title("Exact ten-year net profit — demo-cost scenario")
    for bar, value in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2, value, f"${value:,.0f}", ha="center", va="bottom" if value >= 0 else "top")
    plt.tight_layout()
    fig.savefig(OUT_DIR / "exact_10_year_demo_profit.png", dpi=170)
    plt.close(fig)

    fig = plt.figure(figsize=(10, 6))
    for model in MODELS:
        data = sorted(
            [x for x in yearly if x["window"] == "exact_10_year" and x["cost_scenario"] == "demo_cost" and x["model"] == model],
            key=lambda x: int(x["year"]),
        )
        plt.plot(
            [int(x["year"]) for x in data],
            [float(x["ending_equity"]) for x in data],
            marker="o",
            label="Enhanced live V14.3" if model == "current_live_v14_3" else "V14.5.1",
        )
    plt.axhline(5_000.0, linewidth=1)
    plt.xlabel("Exit year")
    plt.ylabel("Ending equity ($)")
    plt.title("Exact ten-year equity — demo-cost scenario")
    plt.legend()
    plt.tight_layout()
    fig.savefig(OUT_DIR / "exact_10_year_demo_equity.png", dpi=170)
    plt.close(fig)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    live_v12, live_ict, new_v12, new_ict, selection = prepare_models()
    latest = max(live_v12["exit_time"].max(), live_ict["exit_time"].max())
    ten_start = latest - pd.DateOffset(years=10)
    windows = {
        "full_history": (None, latest),
        "exact_10_year": (ten_start, latest),
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
    observation_registry = v14_5_1_registry(live_ict)
    results: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    yearly: list[dict[str, Any]] = []

    for window, (start, end) in windows.items():
        for cost_name, costs in COST_SCENARIOS.items():
            source_frames = {
                "current_live_v14_3": (live_v12, live_ict, original_registry),
                "v14_5_1": (new_v12, new_ict, observation_registry),
            }
            for model, (v12, ict, registry) in source_frames.items():
                set_ict_risk_registry(registry)
                v12_case = apply_costs(v12, costs["V12"])
                ict_case = apply_costs(ict, costs["ICT"])
                if start is not None:
                    v12_case = filter_window(v12_case, start, end)
                    ict_case = filter_window(ict_case, start, end)
                summary, trades, skipped, events = run_replay(v12_case, ict_case, governor)
                key = f"{window}/{model}/{cost_name}"
                results[key] = {
                    "summary": summary,
                    "by_year": annual_rows(trades, model, cost_name, window),
                    "governor_interventions": int(len(events)),
                }
                rows.append(summary_row(window, cost_name, model, summary))
                yearly.extend(results[key]["by_year"])
                folder = OUT_DIR / "ledgers" / window / cost_name
                folder.mkdir(parents=True, exist_ok=True)
                trades.to_csv(folder / f"{model}_trades.csv", index=False)
                skipped.to_csv(folder / f"{model}_skipped.csv", index=False)
                events.to_csv(folder / f"{model}_governor.csv", index=False)

    set_ict_risk_registry(original_registry)
    actual = results["exact_10_year/current_live_v14_3/zero_cost"]["summary"]["net_profit"]
    if abs(actual - EXPECTED_LIVE_TEN_YEAR_NET) > 0.02:
        raise RuntimeError(f"Live benchmark mismatch: expected {EXPECTED_LIVE_TEN_YEAR_NET}, got {actual}")

    payload = {
        "generated_at": datetime.now().isoformat(),
        "sources": {
            "v12": str(V12_LEDGER.relative_to(ROOT)),
            "ict": str(ICT_SOURCE.relative_to(ROOT)),
        },
        "expected_live_exact_ten_year_net": EXPECTED_LIVE_TEN_YEAR_NET,
        "models": {
            "current_live_v14_3": "enhanced V14.3 satellite research-parity model",
            "v14_5_1": {
                "promoted_v12_engines": sorted(PROMOTED_V12_ENGINES),
                "promoted_risk_percent": V14_5_PROMOTED_RISK_PERCENT,
                "observation_risk_percent": V14_5_OBSERVATION_RISK_PERCENT,
            },
        },
        "cost_scenarios_r": COST_SCENARIOS,
        "windows": {
            name: {"start": (start if start is not None else min(live_v12["entry_time"].min(), live_ict["entry_time"].min())).isoformat(), "end": end.isoformat()}
            for name, (start, end) in windows.items()
        },
        "source_selection": selection,
        "results": results,
    }
    (OUT_DIR / "comparison_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_csv(
        OUT_DIR / "comparison_summary.csv",
        rows,
        [
            "window", "cost_scenario", "model", "starting_balance", "ending_balance",
            "net_profit", "return_percent", "profit_factor", "max_closed_drawdown_percent",
            "stress_drawdown_percent", "closed_trades", "skipped_trades",
        ],
    )
    write_csv(
        OUT_DIR / "yearly_profit_equity.csv",
        yearly,
        ["window", "cost_scenario", "model", "year", "trades", "net_profit", "ending_equity"],
    )
    report(payload, rows, yearly)
    plot(rows, yearly)

    print(json.dumps({
        "exact_ten_year_window": payload["windows"]["exact_10_year"],
        "verified_live_zero_cost_net": actual,
        "exact_ten_year": {
            key: value["summary"]
            for key, value in results.items()
            if key.startswith("exact_10_year/")
        },
        "output": str(OUT_DIR),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
