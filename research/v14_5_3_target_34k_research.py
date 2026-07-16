"""V14.5.3: determine what is required to reach $34,000 ten-year net profit.

This is a feasibility study, not a live configuration. It starts from the
validated V14.5.2 trade filters and preserves:

* the same candidate stream, entries, exits, stops and targets;
* the same promoted engines and 0.025% observation streams;
* the same 7.5/8.5/9.0/9.6 drawdown governor;
* the same ICT admission controls and cost assumptions.

The only searched variable is promoted V12 risk. The search reports:

1. the current 0.75% V14.5.2 result;
2. the maximum result within the current 0.80% parity ceiling;
3. the best result retaining <=9.6% closed DD and <=10% stress DD;
4. the minimum risk that reaches $34,000 net, when one exists;
5. whether $34,000 remains feasible under the preserved drawdown boundary.

No live runner, broker code or execution profile is changed.
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
from mt5_ai_bridge.v14_5_cost_robust_profile import (
    PROMOTED_V12_ENGINES,
    V14_5_OBSERVATION_RISK_PERCENT,
)
from research.v14_3_production_improved_backtest import filter_window
from research.v14_3_satellite_symbol_profit_backtest import run_replay
from research.v14_5_2_profit_filter_backtest import (
    COST_SCENARIOS,
    apply_costs,
    observation_registry,
    prepare_models,
    set_ict_registry,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_5_3_target_34k_output"
TARGET_NET_PROFIT = 34_000.0
STARTING_BALANCE = 5_000.0
CURRENT_PROMOTED_RISK = 0.75
CURRENT_PARITY_CEILING = 0.80
MAX_CLOSED_DD = 9.60
MAX_STRESS_DD = 10.00
SEARCH_RISKS = [round(0.75 + 0.05 * index, 2) for index in range(106)]  # 0.75..6.00


def governor() -> DrawdownGovernor:
    return DrawdownGovernor(
        soft_start_percent=7.50,
        medium_start_percent=8.50,
        defensive_start_percent=9.00,
        hard_stop_percent=9.60,
        soft_multiplier=0.98,
        medium_multiplier=0.82,
        defensive_multiplier=0.50,
        minimum_risk_percent=0.025,
    )


def apply_promoted_risk(frame: pd.DataFrame, promoted_risk: float) -> pd.DataFrame:
    output = frame.copy()
    promoted = output["engine"].astype(str).isin(PROMOTED_V12_ENGINES)
    filtered = output["profit_filter_reason"].notna()
    output.loc[promoted & ~filtered, "risk_percent"] = float(promoted_risk)
    output.loc[~promoted | filtered, "risk_percent"] = V14_5_OBSERVATION_RISK_PERCENT
    return output


def run_case(
    v12: pd.DataFrame,
    ict: pd.DataFrame,
    cost_name: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    promoted_risk: float,
) -> dict[str, Any]:
    v12_case = apply_promoted_risk(v12, promoted_risk)
    v12_case = apply_costs(v12_case, COST_SCENARIOS[cost_name]["V12"])
    ict_case = apply_costs(ict, COST_SCENARIOS[cost_name]["ICT"])
    v12_case = filter_window(v12_case, start, end)
    ict_case = filter_window(ict_case, start, end)
    summary, trades, skipped, events = run_replay(v12_case, ict_case, governor())
    return {
        "cost_scenario": cost_name,
        "promoted_risk_percent": promoted_risk,
        "summary": summary,
        "governor_interventions": int(len(events)),
        "closed_trade_rows": int(len(trades)),
        "skipped_trade_rows": int(len(skipped)),
    }


def safe(item: dict[str, Any]) -> bool:
    summary = item["summary"]
    return (
        float(summary["max_closed_drawdown_percent"]) <= MAX_CLOSED_DD + 1e-9
        and float(summary["stress_drawdown_percent"]) <= MAX_STRESS_DD + 1e-9
    )


def target_reached(item: dict[str, Any]) -> bool:
    return float(item["summary"]["net_profit"]) >= TARGET_NET_PROFIT


def select_results(items: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(items, key=lambda item: item["promoted_risk_percent"])
    within_parity = [
        item for item in ordered
        if item["promoted_risk_percent"] <= CURRENT_PARITY_CEILING + 1e-9
    ]
    safe_items = [item for item in ordered if safe(item)]
    targets = [item for item in ordered if target_reached(item)]
    safe_targets = [item for item in targets if safe(item)]
    current = next(
        item for item in ordered
        if abs(item["promoted_risk_percent"] - CURRENT_PROMOTED_RISK) < 1e-9
    )
    return {
        "current": current,
        "best_within_current_parity_ceiling": max(
            within_parity,
            key=lambda item: float(item["summary"]["net_profit"]),
        ),
        "best_with_preserved_drawdown_boundary": max(
            safe_items,
            key=lambda item: float(item["summary"]["net_profit"]),
        ) if safe_items else None,
        "minimum_risk_reaching_target": targets[0] if targets else None,
        "minimum_safe_risk_reaching_target": safe_targets[0] if safe_targets else None,
        "maximum_tested": ordered[-1],
    }


def flat_row(item: dict[str, Any]) -> dict[str, Any]:
    summary = item["summary"]
    return {
        "cost_scenario": item["cost_scenario"],
        "promoted_risk_percent": item["promoted_risk_percent"],
        "net_profit": summary["net_profit"],
        "ending_balance": summary["ending_balance"],
        "return_percent": summary["return_percent"],
        "profit_factor": summary["profit_factor"],
        "max_closed_drawdown_percent": summary["max_closed_drawdown_percent"],
        "stress_drawdown_percent": summary["stress_drawdown_percent"],
        "closed_trades": summary["closed_trades"],
        "skipped_trades": summary["skipped_ict_trades"],
        "governor_interventions": item["governor_interventions"],
        "target_reached": target_reached(item),
        "preserved_drawdown_boundary": safe(item),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def format_result(item: dict[str, Any] | None) -> str:
    if item is None:
        return "Not reached in the tested range"
    summary = item["summary"]
    return (
        f"risk {item['promoted_risk_percent']:.2f}%, "
        f"net ${float(summary['net_profit']):,.2f}, "
        f"ending ${float(summary['ending_balance']):,.2f}, "
        f"PF {float(summary['profit_factor']):.4f}, "
        f"closed DD {float(summary['max_closed_drawdown_percent']):.4f}%, "
        f"stress DD {float(summary['stress_drawdown_percent']):.4f}%"
    )


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.5.3 — $34,000 Ten-Year Target Feasibility",
        "",
        f"**Target:** ${TARGET_NET_PROFIT:,.0f} net profit from a ${STARTING_BALANCE:,.0f} start, ending at ${STARTING_BALANCE + TARGET_NET_PROFIT:,.0f}.",
        f"**Period:** {payload['window']['start'][:10]} to {payload['window']['end'][:10]}",
        "",
        "The search changes only promoted V12 risk. All V14.5.2 filters, observation streams, entries, exits, stops, targets, ICT controls and the drawdown governor remain unchanged.",
        "",
        "## Results by cost assumption",
        "",
    ]
    for cost_name, selected in payload["selected"].items():
        lines += [
            f"### {cost_name}",
            "",
            f"- Current V14.5.2: {format_result(selected['current'])}",
            f"- Best within the present 0.80% parity ceiling: {format_result(selected['best_within_current_parity_ceiling'])}",
            f"- Best retaining <=9.6% closed DD and <=10% stress DD: {format_result(selected['best_with_preserved_drawdown_boundary'])}",
            f"- Minimum risk reaching $34,000: {format_result(selected['minimum_risk_reaching_target'])}",
            f"- Minimum safe risk reaching $34,000: {format_result(selected['minimum_safe_risk_reaching_target'])}",
            "",
        ]

    lines += [
        "## Interpretation",
        "",
        "A target-reaching row is not automatically a deployable model. Any result above the current 0.80% parity ceiling changes the risk architecture. Any result outside the preserved drawdown boundary fails the present V14.5.2 safety objective.",
        "",
        "This is an R-multiple historical replay with fixed cost assumptions, not a tick-level simulation. Spreads, commission, slippage, swap, gaps and future regime changes can materially reduce live results. Historical performance is not a guarantee.",
    ]
    (OUT / "TARGET_34K_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot(rows: list[dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt

    for cost_name in COST_SCENARIOS:
        subset = [row for row in rows if row["cost_scenario"] == cost_name]
        figure = plt.figure(figsize=(10, 6))
        plt.plot(
            [row["promoted_risk_percent"] for row in subset],
            [row["net_profit"] for row in subset],
        )
        plt.axhline(TARGET_NET_PROFIT, linewidth=1)
        plt.axvline(CURRENT_PROMOTED_RISK, linewidth=1)
        plt.xlabel("Promoted V12 risk (%)")
        plt.ylabel("Ten-year net profit ($)")
        plt.title(f"V14.5.2 risk search — {cost_name}")
        plt.tight_layout()
        figure.savefig(OUT / f"{cost_name}_risk_vs_profit.png", dpi=170)
        plt.close(figure)

        figure = plt.figure(figsize=(10, 6))
        plt.plot(
            [row["promoted_risk_percent"] for row in subset],
            [row["max_closed_drawdown_percent"] for row in subset],
            label="Closed DD",
        )
        plt.plot(
            [row["promoted_risk_percent"] for row in subset],
            [row["stress_drawdown_percent"] for row in subset],
            label="Stress DD",
        )
        plt.axhline(MAX_CLOSED_DD, linewidth=1)
        plt.axhline(MAX_STRESS_DD, linewidth=1)
        plt.xlabel("Promoted V12 risk (%)")
        plt.ylabel("Drawdown (%)")
        plt.title(f"V14.5.2 risk search drawdown — {cost_name}")
        plt.legend()
        plt.tight_layout()
        figure.savefig(OUT / f"{cost_name}_risk_vs_drawdown.png", dpi=170)
        plt.close(figure)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _, v14_5_2, ict, selection = prepare_models()
    latest = max(v14_5_2["exit_time"].max(), ict["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    original_registry = dict(profit_profile.SETUP_RISK_PERCENT)
    set_ict_registry(observation_registry(ict))

    cases: dict[str, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    try:
        for cost_name in COST_SCENARIOS:
            cases[cost_name] = []
            for risk in SEARCH_RISKS:
                item = run_case(v14_5_2, ict, cost_name, start, latest, risk)
                cases[cost_name].append(item)
                rows.append(flat_row(item))
    finally:
        set_ict_registry(original_registry)

    selected = {
        cost_name: select_results(items)
        for cost_name, items in cases.items()
    }
    payload = {
        "generated_at": datetime.now().isoformat(),
        "target": {
            "starting_balance": STARTING_BALANCE,
            "net_profit": TARGET_NET_PROFIT,
            "ending_balance": STARTING_BALANCE + TARGET_NET_PROFIT,
        },
        "window": {"start": start.isoformat(), "end": latest.isoformat()},
        "preserved_constraints": {
            "current_promoted_risk_percent": CURRENT_PROMOTED_RISK,
            "current_parity_ceiling_percent": CURRENT_PARITY_CEILING,
            "observation_risk_percent": V14_5_OBSERVATION_RISK_PERCENT,
            "max_closed_drawdown_percent": MAX_CLOSED_DD,
            "max_stress_drawdown_percent": MAX_STRESS_DD,
            "same_v14_5_2_filters": True,
            "same_entries_stops_targets_exits": True,
            "same_ict_controls": True,
            "same_drawdown_governor": True,
        },
        "source_selection": selection,
        "search_range": {
            "minimum_risk_percent": SEARCH_RISKS[0],
            "maximum_risk_percent": SEARCH_RISKS[-1],
            "step_percent": 0.05,
        },
        "selected": selected,
        "all_results": cases,
    }
    (OUT / "target_34k_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    write_csv(OUT / "target_34k_risk_search.csv", rows)
    write_report(payload)
    plot(rows)

    print(json.dumps({
        "window": payload["window"],
        "target": payload["target"],
        "selected": selected,
        "output": str(OUT),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
