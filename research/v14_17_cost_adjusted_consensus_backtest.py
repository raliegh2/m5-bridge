"""Exact-ten-year V14.17 cost-adjusted consensus replay.

The candidate stream, exits, transaction-cost scenarios and all V14.16 risk
ceilings remain unchanged. V14.17 adds a chronological pre-entry overlay that
uses only trades closed before the candidate entry timestamp.
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
from research.v14_14_extended_cost_backtest import EXTENDED_COST_SCENARIOS
from research.v14_15_unified_reasoning_backtest import _coverage
from research.v14_16_quality_allocation_backtest_run import (
    InputNormalizedQualityAllocationReplay,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_17_cost_adjusted_consensus_output"

V14_16_REFERENCE_NET = {
    "zero_cost": 34690.840749742056,
    "demo_cost": 13435.768959959689,
    "retail_cost": 12082.752130586745,
    "stress_cost": 9582.638768147845,
    "severe_cost": 6409.730227709824,
    "extreme_cost": 4704.993276906465,
}


class CostAdjustedConsensusReplay(InputNormalizedQualityAllocationReplay):
    """V14.16 replay plus prior-closed contextual and correlation controls."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v14_17_controller = CostAdjustedConsensusController(
            self,
            parent_decision=parent_unified_cost_reasoning_decision,
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
            self.v14_17_controller.record_closed(item)

    def run(self):
        # The V14.16 replay resolves this symbol at candidate time. Each replay
        # instance owns a fresh controller so no scenario can contaminate another.
        study.unified_cost_reasoning_decision = self.v14_17_controller.decision
        summary, trades, skipped = super().run()

        # V14.16 appends one cost decision event immediately after each call to
        # the wrapped reasoning function. Preserve order and enrich that audit
        # stream with the complete V14.17 context snapshot.
        for inherited, contextual in zip(
            self.cost_regime_events,
            self.v14_17_controller.events,
        ):
            inherited.update(contextual)

        controller_summary = self.v14_17_controller.summary()
        summary["v14_17_cost_adjusted_consensus"] = controller_summary
        summary["v14_17_context_demotions"] = int(
            controller_summary["action_counts"].get("CONTEXT_DEMOTED", 0)
        )
        summary["v14_17_correlation_caps"] = int(
            controller_summary["action_counts"].get("CORRELATION_CAPPED", 0)
        )
        return summary, trades, skipped


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.17 Cost-Adjusted Consensus",
        "",
        "V14.17 is stacked directly on V14.16. It preserves the candidate stream, full-position exits, six cost scenarios, 0.80% single-trade ceiling, 1.75% ICT ceiling, 3.25% combined ceiling and 9.40% projected-stress buffer.",
        "",
        "## Exact ten-year chronological comparison",
        "",
        "| Scenario | V14.16 net | V14.17 net | Improvement | PF | Closed DD | Stress DD | Trades | Context demotions | Correlation caps |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in EXTENDED_COST_SCENARIOS:
        summary = payload["results"][f"{scenario}/v14_13"]["summary"]
        reference = V14_16_REFERENCE_NET[scenario]
        lines.append(
            f"| {scenario} | ${reference:,.2f} | ${summary['net_profit']:,.2f} | "
            f"${summary['net_profit'] - reference:,.2f} | {summary['profit_factor']:.4f} | "
            f"{summary['max_closed_drawdown_percent']:.4f}% | "
            f"{summary['stress_drawdown_percent']:.4f}% | {summary['closed_trades']} | "
            f"{summary.get('v14_17_context_demotions', 0)} | "
            f"{summary.get('v14_17_correlation_caps', 0)} |"
        )

    lines += [
        "",
        "## Decision design",
        "",
        "- Contextual broker-net expectancy is updated only after a trade closes.",
        "- Evidence is recorded by symbol, engine, setup, direction, UTC hour, session and parent regime.",
        "- A V12 engine/direction sleeve is demoted only after at least 20 prior closed trades, mean net result below -0.05R and PF below 0.95.",
        "- V12-ICT consensus is classified from the latest prior signal on the same symbol; conflict can only make an existing demotion stricter and can never create an uplift.",
        "- Correlation-aware admission caps projected net directional exposure in either currency at 2.40% before the inherited portfolio caps.",
        "- Zero-cost parity is untouched; improvement must come from cost-adjusted scenarios.",
        "",
        "## Exit research boundary",
        "",
        "Exit changes are design-only in V14.17. No stop, take-profit, break-even, trailing, timeout or close-order logic is changed by this branch. Any future exit study must run as a separate shadow backtest and must not feed the main V14.17 result.",
        "",
        "## Live boundary",
        "",
        "Historical replay authorization does not authorize live risk changes. Live use requires reconciled broker-net context evidence with at least 30 direction trades and 40 symbol/mode trades. The branch remains READ_ONLY and controlled demo-forward only.",
        "",
        "Historical modeled performance is not a guarantee of demo or live returns.",
    ]
    (OUT / "V14_17_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    study.OUT = OUT
    study.QualityAllocationReplay = CostAdjustedConsensusReplay
    study.main()

    inherited_path = OUT / "v14_16_results.json"
    payload = json.loads(inherited_path.read_text(encoding="utf-8"))
    payload["model"] = "V14.17_COST_ADJUSTED_CONSENSUS"
    payload["parent_model"] = "V14.16_COST_EFFICIENT_QUALITY_ALLOCATION"
    payload["v14_16_reference_net"] = V14_16_REFERENCE_NET
    payload["chronological_context_only"] = True
    payload["exit_research"] = {
        "implemented": False,
        "boundary": "SEPARATE_SHADOW_DESIGN_ONLY",
    }
    payload["dual_mode_coverage"] = {}
    for scenario in EXTENDED_COST_SCENARIOS:
        ledger = OUT / "ledgers" / scenario / "v14_13_trades.csv"
        payload["dual_mode_coverage"][scenario] = _coverage(ledger)

    target = OUT / "v14_17_results.json"
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    shutil.copy2(inherited_path, OUT / "v14_17_parent_results.json")
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
