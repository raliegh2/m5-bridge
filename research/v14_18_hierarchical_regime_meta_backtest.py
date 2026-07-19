"""Exact-ten-year V14.18 hierarchical regime meta-label replay.

V14.18 preserves the V14.17 candidate stream, cost assumptions, exits and risk
ceilings.  It adds a chronological, no-uplift meta-label layer.  Evidence is
updated only after an executed trade closes; a shadowed candidate is retained in
the diagnostic stream but cannot contaminate future broker-net evidence.
"""
from __future__ import annotations

import json
import shutil
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
from research.v14_14_extended_cost_backtest import EXTENDED_COST_SCENARIOS
from research.v14_15_unified_reasoning_backtest import _coverage
from research.v14_16_quality_allocation_backtest_run import (
    InputNormalizedQualityAllocationReplay,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_18_hierarchical_regime_meta_output"

V14_17_REFERENCE_NET = {
    "zero_cost": 34690.840749742056,
    "demo_cost": 14161.108794204785,
    "retail_cost": 12924.246340512182,
    "stress_cost": 10192.9544249731,
    "severe_cost": 6855.22049360981,
    "extreme_cost": 5179.991825433059,
}

META_LEDGER_COLUMNS = (
    "v14_18_market_regime",
    "v14_18_meta_label",
    "v14_18_meta_reason",
    "v14_18_risk_multiplier",
    "v14_18_hierarchical_score_r",
    "v14_18_hierarchical_confidence",
    "v14_18_mature_negative_nodes",
    "v14_18_effective_trades",
)


class HierarchicalRegimeMetaReplay(InputNormalizedQualityAllocationReplay):
    """V14.17 controls plus the V14.18 no-uplift meta-labeler."""

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

    def close_due(self, now):
        due = [
            dict(item)
            for item in self.active
            if item["exit_time"] <= now
        ]
        due.sort(key=lambda item: (item["exit_time"], item["trade_id"]))
        super().close_due(now)
        for item in due:
            self.v14_18_controller.record_closed(item)

    def run(self):
        # The V14.16 replay resolves this callable at candidate time.  A new
        # controller is created for every scenario, preventing scenario leakage.
        study.unified_cost_reasoning_decision = self.v14_18_controller.decision
        summary, trades, skipped = super().run()

        for inherited, contextual in zip(
            self.cost_regime_events,
            self.v14_18_controller.events,
        ):
            inherited.update(contextual)

        if not trades.empty:
            metadata = [
                self.v14_18_controller.metadata_for(row)
                for row in trades.to_dict("records")
            ]
            for column in META_LEDGER_COLUMNS:
                trades[column] = [record.get(column) for record in metadata]

        controller_summary = self.v14_18_controller.summary()
        labels = controller_summary["label_counts"]
        summary["v14_18_hierarchical_regime_meta"] = controller_summary
        summary["v14_18_full_labels"] = int(labels.get("FULL", 0))
        summary["v14_18_reduced_labels"] = int(labels.get("REDUCED", 0))
        summary["v14_18_observation_labels"] = int(labels.get("OBSERVATION", 0))
        summary["v14_18_shadow_labels"] = int(labels.get("SHADOW", 0))
        return summary, trades, skipped


def meta_attribution(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path)
    if frame.empty or "v14_18_meta_label" not in frame:
        return {}
    output: dict[str, Any] = {}
    for (regime, label), group in frame.groupby(
        ["v14_18_market_regime", "v14_18_meta_label"],
        dropna=False,
    ):
        output[f"{regime}/{label}"] = {
            "trades": int(len(group)),
            "net_profit": float(group["pnl"].sum()),
            "mean_net_r": float(group["r_multiple"].mean()),
            "maximum_risk_percent": float(group["risk_percent"].max()),
        }
    return output


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.18 Hierarchical Regime Meta-Labeler",
        "",
        "V14.18 is stacked directly on V14.17. It classifies every candidate as TREND, RANGE, TRANSITION or DISLOCATED and calculates a broad-to-local chronological posterior. It cannot increase inherited risk and does not modify exits.",
        "",
        "## Exact ten-year chronological comparison",
        "",
        "| Scenario | V14.17 net | V14.18 net | Improvement | PF | Closed DD | Stress DD | Trades | Reduced | Observation | Shadow |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in EXTENDED_COST_SCENARIOS:
        summary = payload["results"][f"{scenario}/v14_13"]["summary"]
        reference = V14_17_REFERENCE_NET[scenario]
        lines.append(
            f"| {scenario} | ${reference:,.2f} | ${summary['net_profit']:,.2f} | "
            f"${summary['net_profit'] - reference:,.2f} | {summary['profit_factor']:.4f} | "
            f"{summary['max_closed_drawdown_percent']:.4f}% | "
            f"{summary['stress_drawdown_percent']:.4f}% | {summary['closed_trades']} | "
            f"{summary.get('v14_18_reduced_labels', 0)} | "
            f"{summary.get('v14_18_observation_labels', 0)} | "
            f"{summary.get('v14_18_shadow_labels', 0)} |"
        )

    lines += [
        "",
        "## Frozen first-stage policy",
        "",
        "- The hierarchy is global -> mode -> market regime -> symbol/mode -> engine/regime -> setup/regime -> direction/regime -> session/regime -> hour/regime.",
        "- Child evidence is shrunk toward its parent using a frozen prior strength of 24 trades.",
        "- FULL is the default and no label can raise inherited risk.",
        "- V14.18 acts only when V14.17 has already assigned REASONING_REDUCED to a V12 candidate.",
        "- REDUCED uses 50% of inherited risk, OBSERVATION uses 25%, and SHADOW sends no broker order.",
        "- ICT/RANGE candidates are classified and audited but remain FULL during the stability phase.",
        "",
        "## Range mean-reversion boundary",
        "",
        "The range mean-reversion engine is intentionally not implemented in V14.18. Its future admission requires a stable V14.18 meta-label package, separate shadow candidates, at least 100 closed shadow trades, positive retail/stress net expectancy, and chronological multi-period stability.",
        "",
        "## Retained safety",
        "",
        "- Exact zero-cost parity.",
        "- 0.80% single-trade ceiling.",
        "- 1.75% ICT and 3.25% combined open-risk ceilings.",
        "- 9.40% projected-stress admission buffer and inherited drawdown governor.",
        "- READ_ONLY and controlled demo-forward only.",
        "",
        "Historical modeled returns do not guarantee future demo or live performance.",
    ]
    (OUT / "V14_18_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    study.OUT = OUT
    study.QualityAllocationReplay = HierarchicalRegimeMetaReplay
    study.main()

    inherited_path = OUT / "v14_16_results.json"
    payload = json.loads(inherited_path.read_text(encoding="utf-8"))
    payload["model"] = "V14.18_HIERARCHICAL_REGIME_META_LABELER"
    payload["parent_model"] = "V14.17_COST_ADJUSTED_CONSENSUS"
    payload["v14_17_reference_net"] = V14_17_REFERENCE_NET
    payload["chronological_hierarchy_only"] = True
    payload["risk_uplift_allowed"] = False
    payload["range_mean_reversion"] = {
        "implemented": False,
        "mode": "DEFERRED_UNTIL_META_STABLE",
        "future_mode": "SHADOW_ONLY",
        "minimum_shadow_trades": 100,
    }
    payload["meta_attribution"] = {}
    payload["dual_mode_coverage"] = {}
    for scenario in EXTENDED_COST_SCENARIOS:
        ledger = OUT / "ledgers" / scenario / "v14_13_trades.csv"
        payload["meta_attribution"][scenario] = meta_attribution(ledger)
        payload["dual_mode_coverage"][scenario] = _coverage(ledger)

    target = OUT / "v14_18_results.json"
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    shutil.copy2(inherited_path, OUT / "v14_18_parent_results.json")
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
