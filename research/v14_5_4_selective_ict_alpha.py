"""V14.5.4 selective ICT alpha research.

This model keeps the validated V14.5.2 swing core unchanged and restores only
ICT subsets that survive cost-adjusted development, confirmation and holdout
checks. The legacy GBP M1 ICT stream remains at 0.025% observation risk because
its 5-7.5 pip stops are structurally cost-sensitive. Candidate profit sleeves
come from the wider-stop H1/H4/D1 ICT shadow engines for EURUSD, AUDUSD and
USDJPY.

Research only: no broker connection or order transmission.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile
from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor
from mt5_ai_bridge.v14_3_profit_preserving_profile import SymbolGuard
from mt5_ai_bridge.v14_3_satellite_symbol_profile import (
    SATELLITE_GUARDS,
    filter_satellite_ict,
)
from mt5_ai_bridge.v14_5_cost_robust_profile import V14_5_OBSERVATION_RISK_PERCENT
from research.v14_3_drawdown_limited_backtest_v2 import AdmissionPreservingReplay
from research.v14_3_five_symbol_ict_10y_backtest import build_new_ict_candidates
from research.v14_3_production_improved_backtest import filter_window
from research.v14_3_satellite_symbol_profit_backtest_v2 import load_raw_shadow_candidates
from research.v14_5_2_profit_filter_backtest import prepare_models

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_5_4_selective_ict_output"
GEN = OUT / "candidate_generation"

STARTING_BALANCE = 5_000.0
OBSERVATION_RISK = float(V14_5_OBSERVATION_RISK_PERCENT)
SELECTIVE_ICT_MAX_RISK = 0.35
TARGET_SYMBOLS = ("EURUSD", "AUDUSD", "USDJPY")

# The wide-stop H1 ICT sleeve uses lower R-cost assumptions than the old M1
# scalp stream. Existing observation ICT retains the original 0.075/0.13R cost.
COST_SCENARIOS = {
    "zero_cost": {"V12": 0.0, "ICT_BASE": 0.0, "ICT_WIDE": 0.0},
    "demo_cost": {"V12": 0.02, "ICT_BASE": 0.075, "ICT_WIDE": 0.04},
    "retail_cost": {"V12": 0.03, "ICT_BASE": 0.13, "ICT_WIDE": 0.07},
}
EXPECTED_V14_5_2 = {
    "zero_cost": 5_186.64,
    "demo_cost": 4_140.56,
    "retail_cost": 3_505.36,
}

# Frozen promotion gates. Selection is based on development + confirmation;
# holdout is evaluated once and must pass before the sleeve is admitted.
GATES = {
    "development": {
        "minimum_trades": 10,
        "demo_net_r": 0.0,
        "demo_profit_factor": 1.10,
        "retail_net_r": 0.0,
        "retail_profit_factor": 1.03,
    },
    "confirmation": {
        "minimum_trades": 6,
        "demo_net_r": 0.0,
        "demo_profit_factor": 1.05,
        "retail_net_r": 0.0,
        "retail_profit_factor": 1.00,
    },
    "holdout": {
        "minimum_trades": 6,
        "demo_net_r": 0.0,
        "demo_profit_factor": 1.05,
        "retail_net_r": 0.0,
        "retail_profit_factor": 1.00,
    },
}


def profit_factor(values: pd.Series) -> float | None:
    values = values.astype(float)
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    if gross_loss <= 0:
        return None if gross_profit <= 0 else float("inf")
    return gross_profit / gross_loss


def segment_stats(frame: pd.DataFrame, cost_r: float) -> dict[str, Any]:
    net = frame["r_multiple"].astype(float) - float(cost_r)
    return {
        "trades": int(len(frame)),
        "net_r": round(float(net.sum()), 6),
        "expectancy_r": round(float(net.mean()), 6) if len(frame) else None,
        "profit_factor": round(float(profit_factor(net) or 0.0), 6),
    }


def split_symbol(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    ordered = frame.sort_values(["entry_time", "exit_time"]).reset_index(drop=True)
    n = len(ordered)
    dev_end = int(n * 0.50)
    confirmation_end = int(n * 0.75)
    return {
        "development": ordered.iloc[:dev_end].copy(),
        "confirmation": ordered.iloc[dev_end:confirmation_end].copy(),
        "holdout": ordered.iloc[confirmation_end:].copy(),
    }


def gate_passed(name: str, stats: dict[str, dict[str, Any]]) -> bool:
    gate = GATES[name]
    demo = stats["demo_cost"]
    retail = stats["retail_cost"]
    return (
        demo["trades"] >= gate["minimum_trades"]
        and demo["net_r"] > gate["demo_net_r"]
        and demo["profit_factor"] >= gate["demo_profit_factor"]
        and retail["net_r"] > gate["retail_net_r"]
        and retail["profit_factor"] >= gate["retail_profit_factor"]
    )


def risk_tier(evidence: dict[str, Any]) -> float:
    development = evidence["segments"]["development"]["demo_cost"]
    confirmation = evidence["segments"]["confirmation"]["demo_cost"]
    min_pf = min(development["profit_factor"], confirmation["profit_factor"])
    min_exp = min(development["expectancy_r"], confirmation["expectancy_r"])
    if min_pf >= 1.60 and min_exp >= 0.15:
        return 0.35
    if min_pf >= 1.35 and min_exp >= 0.08:
        return 0.25
    return 0.15


def evaluate_candidates(candidates: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    evidence: dict[str, Any] = {}
    promoted_frames: list[pd.DataFrame] = []

    for symbol in TARGET_SYMBOLS:
        group = candidates[candidates["symbol"] == symbol].copy()
        splits = split_symbol(group)
        symbol_evidence: dict[str, Any] = {
            "candidate_count": int(len(group)),
            "profile_names": sorted(group["profile"].astype(str).unique().tolist()),
            "segments": {},
        }
        for segment_name, segment in splits.items():
            symbol_evidence["segments"][segment_name] = {
                "zero_cost": segment_stats(segment, 0.0),
                "demo_cost": segment_stats(segment, COST_SCENARIOS["demo_cost"]["ICT_WIDE"]),
                "retail_cost": segment_stats(segment, COST_SCENARIOS["retail_cost"]["ICT_WIDE"]),
            }
        development_pass = gate_passed("development", symbol_evidence["segments"]["development"])
        confirmation_pass = gate_passed("confirmation", symbol_evidence["segments"]["confirmation"])
        holdout_pass = gate_passed("holdout", symbol_evidence["segments"]["holdout"])
        promoted = development_pass and confirmation_pass and holdout_pass
        symbol_evidence.update(
            {
                "development_passed": development_pass,
                "confirmation_passed": confirmation_pass,
                "holdout_passed": holdout_pass,
                "promoted": promoted,
                "risk_percent": risk_tier(symbol_evidence) if promoted else OBSERVATION_RISK,
            }
        )
        evidence[symbol] = symbol_evidence
        if promoted:
            selected = group.copy()
            selected["original_setup"] = selected["setup"].astype(str)
            selected["setup"] = selected["profile"].map(
                lambda profile: f"v14_5_4_{symbol.lower()}_{str(profile).lower()}"
            )
            selected["selective_ict_risk_percent"] = symbol_evidence["risk_percent"]
            selected["ict_cost_class"] = "ICT_WIDE"
            promoted_frames.append(selected)

    if not promoted_frames:
        columns = list(candidates.columns) + [
            "original_setup",
            "selective_ict_risk_percent",
            "ict_cost_class",
        ]
        return pd.DataFrame(columns=columns), evidence
    promoted = pd.concat(promoted_frames, ignore_index=True, sort=False)
    promoted = promoted.sort_values(["entry_time", "symbol", "engine"])
    promoted = promoted.drop_duplicates(
        ["entry_time", "exit_time", "symbol", "engine", "side"]
    ).reset_index(drop=True)
    return promoted, evidence


def candidate_key(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["symbol"].astype(str)
        + "|"
        + frame["engine"].astype(str)
        + "|"
        + frame["entry_time"].astype(str)
        + "|"
        + frame["exit_time"].astype(str)
        + "|"
        + frame["side"].astype(str)
    )


def apply_costs(frame: pd.DataFrame, cost_r: float) -> pd.DataFrame:
    output = frame.copy()
    output["raw_r_multiple"] = output["r_multiple"].astype(float)
    output["cost_r"] = float(cost_r)
    output["r_multiple"] = output["raw_r_multiple"] - float(cost_r)
    return output


def combine_enhanced_ict(base_ict: pd.DataFrame, promoted: pd.DataFrame) -> pd.DataFrame:
    if promoted.empty:
        output = base_ict.copy()
        output["ict_cost_class"] = "ICT_BASE"
        return output
    base = base_ict.copy()
    base["candidate_key"] = candidate_key(base)
    selected = promoted.copy()
    selected["candidate_key"] = candidate_key(selected)
    base = base[~base["candidate_key"].isin(set(selected["candidate_key"]))].copy()
    base["ict_cost_class"] = "ICT_BASE"
    combined = pd.concat([base, selected], ignore_index=True, sort=False)
    return combined.sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)


def install_registry(base_ict: pd.DataFrame, promoted: pd.DataFrame) -> None:
    for row in base_ict[["symbol", "setup"]].drop_duplicates().itertuples(index=False):
        profit_profile.SETUP_RISK_PERCENT[(str(row.symbol).upper(), str(row.setup))] = OBSERVATION_RISK
    for row in promoted[["symbol", "setup", "selective_ict_risk_percent"]].drop_duplicates().itertuples(index=False):
        profit_profile.SETUP_RISK_PERCENT[(str(row.symbol).upper(), str(row.setup))] = float(
            row.selective_ict_risk_percent
        )
    # Existing satellite guards are deliberately retained. Each promoted wide
    # ICT symbol remains limited to one position and one entry per hour.
    profit_profile.SYMBOL_GUARDS.update(SATELLITE_GUARDS)


def cost_adjusted_ict(frame: pd.DataFrame, costs: dict[str, float]) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for cost_class, group in frame.groupby("ict_cost_class", dropna=False):
        name = str(cost_class) if pd.notna(cost_class) else "ICT_BASE"
        pieces.append(apply_costs(group, costs[name]))
    return pd.concat(pieces, ignore_index=True, sort=False).sort_values(
        ["entry_time", "symbol", "engine"]
    )


def run_replay(v12: pd.DataFrame, ict: pd.DataFrame, governor: DrawdownGovernor):
    replay = AdmissionPreservingReplay(v12, ict, governor)
    summary, trades, skipped = replay.run()
    return summary, trades, skipped, pd.DataFrame(replay.governor_events)


def yearly_rows(trades: pd.DataFrame, model: str, cost_name: str) -> list[dict[str, Any]]:
    if trades.empty:
        return []
    frame = trades.copy()
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["year"] = frame["exit_time"].dt.year
    rows: list[dict[str, Any]] = []
    for year, group in frame.groupby("year", sort=True):
        ordered = group.sort_values(["exit_time", "trade_id"])
        rows.append(
            {
                "model": model,
                "cost_scenario": cost_name,
                "year": int(year),
                "trades": int(len(group)),
                "net_profit": float(group["pnl"].sum()),
                "ending_equity": float(ordered.iloc[-1]["equity_after"]),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(payload: dict[str, Any], summary_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# V14.5.4 Selective ICT Alpha Backtest",
        "",
        f"**Exact ten-year period:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        "**Starting balance:** $5,000.00",
        "",
        "V14.5.4 keeps V14.5.2 as the swing core. The old GBP M1 ICT stream remains at 0.025% observation risk. Only wider-stop H1 ICT subsets that passed cost-adjusted development, confirmation and holdout gates receive a profit allocation.",
        "",
        "## ICT selection",
        "",
        "| Symbol | Candidates | Development | Confirmation | Holdout | Promoted risk |",
        "|---|---:|---|---|---|---:|",
    ]
    for symbol, evidence in payload["ict_evidence"].items():
        lines.append(
            f"| {symbol} | {evidence['candidate_count']} | {evidence['development_passed']} | "
            f"{evidence['confirmation_passed']} | {evidence['holdout_passed']} | "
            f"{evidence['risk_percent']:.3f}% |"
        )
    lines += [
        "",
        "## Exact ten-year portfolio results",
        "",
        "| Costs | Model | Net profit | Ending balance | PF | Max closed DD | Stress DD | Closed trades |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for cost_name in COST_SCENARIOS:
        for model in ("v14_5_2", "v14_5_4"):
            row = next(
                item
                for item in summary_rows
                if item["cost_scenario"] == cost_name and item["model"] == model
            )
            label = "V14.5.2" if model == "v14_5_2" else "V14.5.4 selective ICT"
            lines.append(
                f"| {cost_name} | {label} | ${row['net_profit']:,.2f} | ${row['ending_balance']:,.2f} | "
                f"{row['profit_factor']:.4f} | {row['max_closed_drawdown_percent']:.4f}% | "
                f"{row['stress_drawdown_percent']:.4f}% | {row['closed_trades']} |"
            )
    lines += [
        "",
        "## Preserved protections",
        "",
        "- V14.5.2 swing entries, filters, stops, targets and 0.75% promoted risk remain unchanged.",
        "- Legacy GBP M1 ICT remains observation-only because it failed transaction-cost validation.",
        "- Selective ICT risk is capped at 0.35% per trade.",
        "- The 1.75% ICT and 3.25% combined open-risk caps remain active.",
        "- The 7.5/8.5/9.0/9.6 drawdown governor remains active.",
        "- Each new ICT symbol retains one-position and one-entry-per-hour controls.",
        "",
        "## Limitations",
        "",
        "The replay uses fixed R-cost assumptions rather than tick-level broker fills. The H1 ICT sleeve is wider-stop and structurally different from the old M1 scalp stream. Historical performance does not guarantee future results; the sleeve must remain in READ_ONLY/demo forward validation before any AUTO integration.",
    ]
    (OUT / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_results(summary_rows: list[dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt

    demo = [row for row in summary_rows if row["cost_scenario"] == "demo_cost"]
    labels = ["V14.5.2" if row["model"] == "v14_5_2" else "V14.5.4" for row in demo]
    profits = [float(row["net_profit"]) for row in demo]
    figure = plt.figure(figsize=(8, 5))
    bars = plt.bar(labels, profits)
    plt.axhline(0, linewidth=1)
    plt.ylabel("Net profit ($)")
    plt.title("Exact ten-year demo-cost profit")
    for bar, value in zip(bars, profits):
        plt.text(bar.get_x() + bar.get_width() / 2, value, f"${value:,.0f}", ha="center", va="bottom")
    plt.tight_layout()
    figure.savefig(OUT / "demo_profit_comparison.png", dpi=170)
    plt.close(figure)

    drawdowns = [float(row["max_closed_drawdown_percent"]) for row in demo]
    figure = plt.figure(figsize=(8, 5))
    bars = plt.bar(labels, drawdowns)
    plt.ylabel("Maximum closed drawdown (%)")
    plt.title("Exact ten-year demo-cost drawdown")
    for bar, value in zip(bars, drawdowns):
        plt.text(bar.get_x() + bar.get_width() / 2, value, f"{value:.2f}%", ha="center", va="bottom")
    plt.tight_layout()
    figure.savefig(OUT / "demo_drawdown_comparison.png", dpi=170)
    plt.close(figure)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _, v14_5_2_v12, base_ict, base_selection = prepare_models()

    # Rebuild the raw H1 shadow candidates, then apply the previously frozen
    # quality filters before cost-adjusted three-way validation.
    build_new_ict_candidates(GEN)
    raw_shadow = load_raw_shadow_candidates(GEN)
    filtered_shadow = filter_satellite_ict(raw_shadow)
    filtered_shadow.to_csv(OUT / "filtered_wide_ict_candidates.csv", index=False)
    promoted, evidence = evaluate_candidates(filtered_shadow)
    promoted.to_csv(OUT / "promoted_selective_ict_candidates.csv", index=False)

    base = base_ict.copy()
    base["ict_cost_class"] = "ICT_BASE"
    enhanced = combine_enhanced_ict(base, promoted)

    latest = max(
        v14_5_2_v12["exit_time"].max(),
        base["exit_time"].max(),
        enhanced["exit_time"].max(),
    )
    start = latest - pd.DateOffset(years=10)
    v12_window = filter_window(v14_5_2_v12, start, latest)
    base_window = filter_window(base, start, latest)
    enhanced_window = filter_window(enhanced, start, latest)

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
    original_guards = dict(profit_profile.SYMBOL_GUARDS)

    results: dict[str, Any] = {}
    summary_rows: list[dict[str, Any]] = []
    annual: list[dict[str, Any]] = []

    try:
        for cost_name, costs in COST_SCENARIOS.items():
            v12_case = apply_costs(v12_window, costs["V12"])

            # Control: current V14.5.2 observation-only ICT.
            profit_profile.SETUP_RISK_PERCENT.clear()
            profit_profile.SETUP_RISK_PERCENT.update(original_registry)
            install_registry(base_window, pd.DataFrame(columns=promoted.columns))
            base_case = cost_adjusted_ict(base_window, costs)
            base_summary, base_trades, base_skipped, base_events = run_replay(
                v12_case, base_case, governor
            )

            # Enhanced: replace passing H1 candidates with selective risk tiers.
            profit_profile.SETUP_RISK_PERCENT.clear()
            profit_profile.SETUP_RISK_PERCENT.update(original_registry)
            install_registry(base_window, promoted)
            enhanced_case = cost_adjusted_ict(enhanced_window, costs)
            new_summary, new_trades, new_skipped, new_events = run_replay(
                v12_case, enhanced_case, governor
            )

            for model, summary, trades, skipped, events in (
                ("v14_5_2", base_summary, base_trades, base_skipped, base_events),
                ("v14_5_4", new_summary, new_trades, new_skipped, new_events),
            ):
                key = f"{model}/{cost_name}"
                results[key] = {
                    "summary": summary,
                    "governor_interventions": int(len(events)),
                }
                summary_rows.append(
                    {
                        "model": model,
                        "cost_scenario": cost_name,
                        "net_profit": summary["net_profit"],
                        "ending_balance": summary["ending_balance"],
                        "return_percent": summary["return_percent"],
                        "profit_factor": summary["profit_factor"],
                        "max_closed_drawdown_percent": summary["max_closed_drawdown_percent"],
                        "stress_drawdown_percent": summary["stress_drawdown_percent"],
                        "closed_trades": summary["closed_trades"],
                        "skipped_trades": summary["skipped_ict_trades"],
                    }
                )
                annual.extend(yearly_rows(trades, model, cost_name))
                folder = OUT / "ledgers" / cost_name
                folder.mkdir(parents=True, exist_ok=True)
                trades.to_csv(folder / f"{model}_trades.csv", index=False)
                skipped.to_csv(folder / f"{model}_skipped.csv", index=False)
                events.to_csv(folder / f"{model}_governor.csv", index=False)
    finally:
        profit_profile.SETUP_RISK_PERCENT.clear()
        profit_profile.SETUP_RISK_PERCENT.update(original_registry)
        profit_profile.SYMBOL_GUARDS.clear()
        profit_profile.SYMBOL_GUARDS.update(original_guards)

    for cost_name, expected in EXPECTED_V14_5_2.items():
        actual = results[f"v14_5_2/{cost_name}"]["summary"]["net_profit"]
        if abs(float(actual) - expected) > 0.10:
            raise RuntimeError(
                f"V14.5.2 benchmark mismatch for {cost_name}: expected {expected}, got {actual}"
            )

    payload = {
        "generated_at": datetime.now().isoformat(),
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "starting_balance": STARTING_BALANCE,
        "cost_scenarios_r": COST_SCENARIOS,
        "selection_gates": GATES,
        "ict_evidence": evidence,
        "promoted_symbols": [symbol for symbol, item in evidence.items() if item["promoted"]],
        "promoted_candidate_count": int(len(promoted)),
        "v14_5_2_source_selection": base_selection,
        "results": results,
    }
    (OUT / "v14_5_4_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_csv(
        OUT / "comparison_summary.csv",
        summary_rows,
        [
            "model",
            "cost_scenario",
            "net_profit",
            "ending_balance",
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
        annual,
        [
            "model",
            "cost_scenario",
            "year",
            "trades",
            "net_profit",
            "ending_equity",
        ],
    )
    write_report(payload, summary_rows)
    plot_results(summary_rows)

    print(
        json.dumps(
            {
                "window": payload["window"],
                "promoted_symbols": payload["promoted_symbols"],
                "ict_evidence": evidence,
                "results": {key: value["summary"] for key, value in results.items()},
                "output": str(OUT),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
