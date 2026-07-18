"""Exact-ten-year V14.15 unified dual-engine reasoning replay.

The verified V14.14 chronology, accounting, costs and risk controls are reused.
Only the pre-entry cost/reasoning decision is replaced.  Existing V14.14-funded
trades remain eligible; bounded probation profiles add evidence collection for
symbol/mode combinations that were previously entirely shadowed.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

import research.v14_13_cost_regime_backtest as base
from mt5_ai_bridge.v14_14_extended_cost_profile import ExtendedCostRegimeConfig
from mt5_ai_bridge.v14_15_unified_reasoning import (
    DUAL_ENGINE_REGISTRY,
    probation_profile,
    unified_cost_reasoning_decision,
)
from research.v14_14_extended_cost_backtest import (
    EXTENDED_COST_SCENARIOS,
    extended_profile_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_15_unified_reasoning_output"

V14_14_REFERENCE_NET = {
    "zero_cost": 34690.840749742056,
    "demo_cost": 7832.04,
    "retail_cost": 6956.20,
    "stress_cost": 5482.54,
    "severe_cost": 4069.61,
    "extreme_cost": 2672.53,
}


def _coverage(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path)
    if frame.empty:
        return {symbol: {"V12": 0, "ICT": 0} for symbol in DUAL_ENGINE_REGISTRY}
    grouped = frame.groupby(["symbol", "engine_group"]).size().to_dict()
    return {
        symbol: {
            mode: int(grouped.get((symbol, mode), 0))
            for mode in ("V12", "ICT")
        }
        for symbol in DUAL_ENGINE_REGISTRY
    }


def _probation_attribution(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path)
    if frame.empty or "cost_regime" not in frame:
        return {}
    selected = frame[frame["cost_regime"] == "DUAL_ENGINE_PROBATION"].copy()
    output: dict[str, Any] = {}
    for (symbol, mode, engine), group in selected.groupby(
        ["symbol", "engine_group", "engine"]
    ):
        wins = group.loc[group["pnl"] > 0, "pnl"].sum()
        losses = -group.loc[group["pnl"] < 0, "pnl"].sum()
        output[f"{symbol}/{mode}/{engine}"] = {
            "trades": int(len(group)),
            "net_profit": float(group["pnl"].sum()),
            "profit_factor": float(wins / losses) if losses > 0 else None,
            "mean_net_r": float(group["r_multiple"].mean()),
        }
    return output


def write_unified_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.15 Unified Dual-Engine Reasoning Backtest",
        "",
        "The replay preserves the verified V14.14 cost policy and adds bounded probation profiles so every symbol can retain both V12 and ICT participation. No probation trade exceeds its original strategy risk, and most recovery profiles use materially less risk.",
        "",
        "## Exact ten-year comparison",
        "",
        "| Scenario | V14.14 net | V14.15 net | Change | PF | Closed DD | Stress DD | Trades |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    results = payload["results"]
    for scenario in EXTENDED_COST_SCENARIOS:
        summary = results[f"{scenario}/v14_13"]["summary"]
        reference = V14_14_REFERENCE_NET[scenario]
        lines.append(
            f"| {scenario} | ${reference:,.2f} | ${summary['net_profit']:,.2f} | "
            f"${summary['net_profit'] - reference:,.2f} | {summary['profit_factor']:.4f} | "
            f"{summary['max_closed_drawdown_percent']:.4f}% | "
            f"{summary['stress_drawdown_percent']:.4f}% | {summary['closed_trades']} |"
        )

    lines += [
        "",
        "## Dual-engine funded coverage",
        "",
        "| Scenario | Symbol | V12 trades | ICT trades |",
        "|---|---|---:|---:|",
    ]
    for scenario, coverage in payload["dual_mode_coverage"].items():
        for symbol, modes in coverage.items():
            lines.append(
                f"| {scenario} | {symbol} | {modes['V12']} | {modes['ICT']} |"
            )

    lines += [
        "",
        "## Probation attribution",
        "",
        "Probation profiles are research evidence collectors. Positive historical attribution does not authorize automatic risk promotion; live broker-net evidence is still required.",
        "",
    ]
    for scenario, items in payload["probation_attribution"].items():
        lines += [
            f"### {scenario}",
            "",
            "| Symbol/mode/engine | Trades | Net | PF | Mean net R |",
            "|---|---:|---:|---:|---:|",
        ]
        for key, stats in sorted(items.items()):
            pf = stats["profit_factor"]
            lines.append(
                f"| {key} | {stats['trades']} | ${stats['net_profit']:,.2f} | "
                f"{float(pf or 0):.3f} | {stats['mean_net_r']:.4f} |"
            )
        lines.append("")

    lines += [
        "## Live reasoning not represented by hindsight",
        "",
        "The live executor additionally uses reconciled broker-net rolling R for each engine and symbol/mode pair. It reduces or shadows mature negative evidence and blocks same-symbol V12/ICT directional conflicts. Those live adaptations require forward observations and are not credited with hypothetical backtest profit here.",
        "",
        "Historical performance is not a guarantee. The branch remains READ_ONLY/demo-forward only.",
    ]
    (OUT / "V14_15_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    base.OUT = OUT
    base.GEN = OUT / "generated_candidates"
    base.COST_SCENARIOS = EXTENDED_COST_SCENARIOS
    base.CostRegimeConfig = ExtendedCostRegimeConfig
    base.cost_regime_decision = unified_cost_reasoning_decision
    base.strict_profile_evidence = extended_profile_evidence
    base.main()

    source = OUT / "v14_13_results.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["model"] = "V14.15_UNIFIED_DUAL_ENGINE_REASONING"
    payload["dual_engine_registry"] = DUAL_ENGINE_REGISTRY
    payload["probation_profiles_are_pre_entry"] = True
    payload["dual_mode_coverage"] = {}
    payload["probation_attribution"] = {}

    for scenario in EXTENDED_COST_SCENARIOS:
        ledger = OUT / "ledgers" / scenario / "v14_13_trades.csv"
        payload["dual_mode_coverage"][scenario] = _coverage(ledger)
        payload["probation_attribution"][scenario] = _probation_attribution(ledger)

    target = OUT / "v14_15_results.json"
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    shutil.copy2(source, OUT / "v14_15_base_results.json")
    write_unified_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
