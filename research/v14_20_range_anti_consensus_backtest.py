"""Exact-ten-year V14.20 range anti-consensus integration replay."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import research.v14_16_quality_allocation_backtest as study
from mt5_ai_bridge.v14_15_unified_reasoning import (
    unified_cost_reasoning_decision as parent_unified_cost_reasoning_decision,
)
from mt5_ai_bridge.v14_17_cost_adjusted_consensus import (
    CostAdjustedConsensusController,
)
from mt5_ai_bridge.v14_18_hierarchical_regime_meta import (
    HierarchicalRegimeMetaLabeler,
)
from mt5_ai_bridge.v14_20_range_anti_consensus import (
    RangeAntiConsensusController,
)
from research.v14_14_extended_cost_backtest import EXTENDED_COST_SCENARIOS
from research.v14_15_unified_reasoning_backtest import _coverage
from research.v14_16_quality_allocation_backtest_run import (
    InputNormalizedQualityAllocationReplay,
)
from research.v14_19_range_shadow_backtest import (
    V14_18_REFERENCE_NET,
    apply_scenario_reserve,
    block_stats,
    build_shadow_source,
    ratio_stats,
    symbol_stats,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_20_range_anti_consensus_output"

V14_19_REFERENCE_NET = dict(V14_18_REFERENCE_NET)

V14_20_LEDGER_COLUMNS = (
    "v14_20_range_relation",
    "v14_20_range_signal_side",
    "v14_20_range_signal_entry_time",
    "v14_20_range_signal_exit_time",
    "v14_20_action",
    "v14_20_reason",
    "v14_20_parent_risk_percent",
    "v14_20_final_risk_percent",
    "v14_20_engine_conflict_trades",
    "v14_20_engine_conflict_mean_r",
    "v14_20_engine_conflict_profit_factor",
)


class RangeAntiConsensusReplay(InputNormalizedQualityAllocationReplay):
    """V14.18 controls plus V14.20 active range-conflict loss filtering."""

    range_source = pd.DataFrame()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v14_17_controller = CostAdjustedConsensusController(
            self,
            parent_decision=parent_unified_cost_reasoning_decision,
        )
        self.v14_18_controller = HierarchicalRegimeMetaLabeler(
            self.v14_17_controller,
            self,
        )
        self.v14_20_controller = RangeAntiConsensusController(
            self.v14_18_controller,
            self.range_source,
        )

    def close_due(self, now):
        due = [
            dict(item)
            for item in self.active
            if item["exit_time"] <= now
        ]
        due.sort(key=lambda item: (item["exit_time"], item["trade_id"]))
        InputNormalizedQualityAllocationReplay.close_due(self, now)
        for item in due:
            self.v14_20_controller.record_closed(item)

    def run(self):
        study.unified_cost_reasoning_decision = self.v14_20_controller.decision
        summary, trades, skipped = InputNormalizedQualityAllocationReplay.run(self)

        for inherited, contextual in zip(
            self.cost_regime_events,
            self.v14_20_controller.events,
        ):
            inherited.update(contextual)

        if not trades.empty:
            metadata = [
                self.v14_20_controller.metadata_for(row)
                for row in trades.to_dict("records")
            ]
            for column in V14_20_LEDGER_COLUMNS:
                trades[column] = [record.get(column) for record in metadata]

        controller_summary = self.v14_20_controller.summary()
        actions = controller_summary["action_counts"]
        relations = controller_summary["relation_counts"]
        summary["v14_20_range_anti_consensus"] = controller_summary
        summary["v14_20_shadow_actions"] = int(actions.get("SHADOW", 0))
        summary["v14_20_unchanged_actions"] = int(actions.get("UNCHANGED", 0))
        summary["v14_20_conflict_candidates"] = int(relations.get("CONFLICT", 0))
        summary["v14_20_aligned_candidates"] = int(relations.get("ALIGNED", 0))
        return summary, trades, skipped


def decision_attribution(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path)
    if frame.empty or "v14_20_action" not in frame:
        return {}
    output: dict[str, Any] = {
        "actions": frame["v14_20_action"].fillna("UNKNOWN").value_counts().to_dict(),
        "relations": frame["v14_20_range_relation"].fillna("UNKNOWN").value_counts().to_dict(),
        "shadow_by_engine": {},
    }
    shadow = frame[frame["v14_20_action"].astype(str) == "SHADOW"]
    for engine, group in shadow.groupby("engine", dropna=False):
        output["shadow_by_engine"][str(engine)] = {
            "candidates": int(len(group)),
            "minimum_prior_conflict_trades": int(
                pd.to_numeric(
                    group["v14_20_engine_conflict_trades"],
                    errors="coerce",
                ).min()
            ),
            "maximum_prior_conflict_mean_r": float(
                pd.to_numeric(
                    group["v14_20_engine_conflict_mean_r"],
                    errors="coerce",
                ).max()
            ),
            "maximum_prior_conflict_profit_factor": float(
                pd.to_numeric(
                    group["v14_20_engine_conflict_profit_factor"],
                    errors="coerce",
                ).max()
            ),
        }
    return output


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.20 Range Anti-Consensus Integration",
        "",
        "V14.20 keeps every direct D1 range mean-reversion trade at zero risk. The active range signal is integrated only as a chronological context for the existing V12/ICT portfolio. When the same principal engine has mature negative broker-net results while trading opposite an active range signal, that candidate is shadowed.",
        "",
        "## Exact ten-year six-scenario comparison",
        "",
        "| Scenario | V14.19 net | V14.20 net | Improvement | PF | Closed DD | Stress DD | Trades | V14.20 shadows |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in EXTENDED_COST_SCENARIOS:
        summary = payload["results"][f"{scenario}/v14_13"]["summary"]
        reference = payload["principal_reference_net"][scenario]
        lines.append(
            f"| {scenario} | ${reference:,.2f} | ${summary['net_profit']:,.2f} | "
            f"${summary['net_profit'] - reference:,.2f} | "
            f"{summary['profit_factor']:.4f} | "
            f"{summary['max_closed_drawdown_percent']:.4f}% | "
            f"{summary['stress_drawdown_percent']:.4f}% | "
            f"{summary['closed_trades']} | "
            f"{summary.get('v14_20_shadow_actions', 0)} |"
        )

    lines += [
        "",
        "## Frozen integration rule",
        "",
        "- Direct range risk remains 0.00%; no range trade is transmitted.",
        "- An active range signal is ALIGNED, CONFLICT or UNAVAILABLE relative to the principal candidate.",
        "- Only CONFLICT contexts can activate the loss filter.",
        "- Evidence uses the same principal engine's last 20 executed conflict trades.",
        "- At least 10 already-closed trades are required.",
        "- The rolling mean must be below 0R and profit factor below 0.80.",
        "- A qualifying candidate is shadowed; risk is never increased.",
        "- Shadowed candidates do not update future broker-net evidence.",
        "- Zero-cost decisions remain exactly unchanged.",
        "",
        "## Live boundary",
        "",
        "The repository live runner is unchanged. A future controlled demo adapter must additionally confirm range-feed parity, chronological broker reconciliation and at least 20 live engine-conflict trades before applying the risk reduction.",
        "",
        "Historical modeled results do not guarantee future demo or live performance.",
    ]
    (OUT / "V14_20_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    source = build_shadow_source()
    source.to_csv(OUT / "range_shadow_source_trades.csv", index=False)
    RangeAntiConsensusReplay.range_source = source

    study.OUT = OUT
    study.QualityAllocationReplay = RangeAntiConsensusReplay
    study.main()

    inherited_path = OUT / "v14_16_results.json"
    payload = json.loads(inherited_path.read_text(encoding="utf-8"))
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["model"] = "V14.20_RANGE_ANTI_CONSENSUS_INTEGRATION"
    payload["parent_model"] = "V14.19_RANGE_MEAN_REVERSION_SHADOW"
    payload["research_only"] = True
    payload["live_execution_changed"] = False
    payload["principal_portfolio_changed"] = True
    payload["risk_uplift_allowed"] = False
    payload["direct_range_risk_percent"] = 0.0
    payload["principal_reference_net"] = V14_19_REFERENCE_NET
    payload["anti_consensus_policy"] = {
        "active_relation": "CONFLICT",
        "evidence_scope": "EXACT_PRINCIPAL_ENGINE",
        "rolling_window": 20,
        "minimum_closed_trades": 10,
        "maximum_mean_r": 0.0,
        "maximum_profit_factor": 0.80,
        "action": "SHADOW",
        "shadow_updates_evidence": False,
        "zero_cost_active": False,
    }

    payload["decision_attribution"] = {}
    payload["dual_mode_coverage"] = {}
    payload["range_shadow"] = {
        "source_trades": int(len(source)),
        "direct_risk_percent": 0.0,
        "transmission_authorized": False,
        "scenarios": {},
    }
    for scenario, costs in EXTENDED_COST_SCENARIOS.items():
        decision_path = (
            OUT
            / "ledgers"
            / scenario
            / "v14_13_cost_regime_decisions.csv"
        )
        trade_path = OUT / "ledgers" / scenario / "v14_13_trades.csv"
        payload["decision_attribution"][scenario] = decision_attribution(
            decision_path
        )
        payload["dual_mode_coverage"][scenario] = _coverage(trade_path)

        reserve = float(costs["V12"])
        range_ledger = apply_scenario_reserve(
            source,
            scenario=scenario,
            additional_cost_r=reserve,
        )
        range_ledger.to_csv(
            OUT / f"range_shadow_{scenario}_trades.csv",
            index=False,
        )
        payload["range_shadow"]["scenarios"][scenario] = {
            "additional_cost_r": reserve,
            "summary": ratio_stats(range_ledger),
            "blocks": block_stats(range_ledger),
            "symbols": symbol_stats(range_ledger),
        }

    target = OUT / "v14_20_results.json"
    target.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )
    shutil.copy2(
        inherited_path,
        OUT / "v14_20_parent_raw_results.json",
    )
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
