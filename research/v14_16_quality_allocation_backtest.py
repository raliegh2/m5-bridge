"""Exact-ten-year V14.16 cost-efficient quality-allocation replay.

V14.15 remains the signal, cost and reasoning baseline.  V14.16 changes only
risk allocation for frozen quality profiles already admitted at full strength.
The 0.80% single-trade limit, 1.75% ICT cap, 3.25% combined cap and the complete
drawdown/loss-control path remain active.
"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import research.v14_13_cost_regime_backtest as base
from mt5_ai_bridge.v14_3_profit_preserving_profile import (
    PORTFOLIO_GUARD,
    SETUP_RISK_PERCENT,
    scaled_risk_percent,
)
from mt5_ai_bridge.v14_14_extended_cost_profile import ExtendedCostRegimeConfig
from mt5_ai_bridge.v14_15_unified_reasoning import unified_cost_reasoning_decision
from mt5_ai_bridge.v14_16_quality_allocation import (
    QUALITY_RISK_PERCENT,
    apply_quality_allocation,
    quality_risk_target,
)
from research.v14_14_extended_cost_backtest import (
    EXTENDED_COST_SCENARIOS,
    extended_profile_evidence,
)
from research.v14_3_production_improved_backtest import summarize
from research.v14_15_unified_reasoning_backtest import _coverage

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v14_16_quality_allocation_output"

V14_15_REFERENCE_NET = {
    "zero_cost": 34690.840749742056,
    "demo_cost": 7895.01,
    "retail_cost": 7189.08,
    "stress_cost": 5652.20,
    "severe_cost": 4185.77,
    "extreme_cost": 2891.88,
}


class QualityAllocationReplay(base.CostRegimeReplay):
    """Chronological replay with bounded uplift for full-strength profiles."""

    def run(self):
        stream = [
            (row["entry_time"], "V12", row)
            for row in self.v12.to_dict("records")
        ]
        stream += [
            (row["entry_time"], "ICT", row)
            for row in self.ict.to_dict("records")
        ]
        stream.sort(key=lambda item: (item[0], 0 if item[1] == "V12" else 1))

        for now, group, row in stream:
            self.close_due(now)
            self.reset_day(now)
            total_open = sum(item["risk_percent"] for item in self.active)
            ict_open = sum(
                item["risk_percent"]
                for item in self.active
                if item["engine_group"] == "ICT"
            )

            if group == "V12":
                nominal_risk = float(row["risk_percent"])
                base_risk = nominal_risk
            else:
                blocked = self.reject_reason(row, now)
                if blocked:
                    self.skipped.append({**row, "skip_reason": blocked})
                    continue
                symbol = str(row["symbol"])
                nominal_risk = float(
                    SETUP_RISK_PERCENT.get(
                        (symbol, str(row["setup"])),
                        row["risk_percent"],
                    )
                )
                pressure = (
                    self.day.global_consecutive_losses > 0
                    or self.day.loss_pressure[symbol] > 0
                    or self.day.daily_pnl[symbol] < 0
                )
                base_risk = scaled_risk_percent(
                    symbol,
                    str(row["setup"]),
                    self.dd(),
                    pressure,
                )

            cost_r = self.costs[group]
            target_r = self._target_r(group, row)
            current = unified_cost_reasoning_decision(
                symbol=str(row["symbol"]),
                engine=str(row["engine"]),
                setup=str(row["setup"]),
                mode=group,
                side=str(row.get("side", "")),
                entry_time=now,
                base_risk_percent=base_risk,
                all_in_cost=cost_r,
                target_r=target_r,
                config=self.cost_config,
            )
            pre_quality = min(base_risk, float(current.risk_percent))
            target, quality_reason = quality_risk_target(
                symbol=str(row["symbol"]),
                engine=str(row["engine"]),
                setup=str(row["setup"]),
                mode=group,
                side=str(row.get("side", "")),
                entry_time=now,
                all_in_cost_r=cost_r,
                nominal_risk_percent=nominal_risk,
                current_risk_percent=pre_quality,
                current_decision=current,
                historical_profile_authorized=True,
            )
            decision = apply_quality_allocation(
                current,
                target_risk_percent=target,
                reason=quality_reason,
            )
            requested = (
                pre_quality
                if target is None
                else min(QUALITY_RISK_PERCENT, float(target))
            )

            self.cost_regime_events.append(
                {
                    "entry_time": now,
                    "symbol": row["symbol"],
                    "engine": row["engine"],
                    "setup": row["setup"],
                    "side": row.get("side", ""),
                    "nominal_risk_percent": nominal_risk,
                    "pre_quality_risk_percent": pre_quality,
                    "quality_reason": quality_reason,
                    **decision.__dict__,
                }
            )
            if decision.is_shadow or requested <= 0:
                self.skipped.append(
                    {
                        **row,
                        "skip_reason": "V14_16_REASONING_SHADOW",
                        "cost_regime_reason": decision.reason,
                        "all_in_cost_r": cost_r,
                    }
                )
                continue

            quality_allocated = decision.regime == "QUALITY_ALLOCATED"
            if requested > QUALITY_RISK_PERCENT + 1e-12:
                self.skipped.append({**row, "skip_reason": "QUALITY_RISK_LIMIT"})
                continue

            if group == "ICT":
                if (
                    ict_open + requested
                    > PORTFOLIO_GUARD.max_ict_open_risk_percent + 1e-12
                ):
                    self.skipped.append({**row, "skip_reason": "ICT_OPEN_RISK_CAP"})
                    continue
                if (
                    total_open + requested
                    > PORTFOLIO_GUARD.max_combined_open_risk_percent + 1e-12
                ):
                    self.skipped.append({**row, "skip_reason": "COMBINED_OPEN_RISK_CAP"})
                    continue
            elif quality_allocated and (
                total_open + requested
                > PORTFOLIO_GUARD.max_combined_open_risk_percent + 1e-12
            ):
                self.skipped.append({**row, "skip_reason": "COMBINED_OPEN_RISK_CAP"})
                continue

            current_dd = self.dd()
            approved = self.governor.apply(requested, current_dd)
            if approved <= 0:
                self.skipped.append(
                    {**row, "skip_reason": "DRAWDOWN_GOVERNOR_HARD_STOP"}
                )
                continue
            if approved < requested - 1e-12:
                self.governor_events.append(
                    {
                        "entry_time": now,
                        "symbol": row["symbol"],
                        "engine": row["engine"],
                        "drawdown_percent": current_dd,
                        "requested_risk_percent": requested,
                        "approved_risk_percent": approved,
                        "multiplier": approved / requested if requested else 0.0,
                    }
                )

            raw_r = float(row["r_multiple"])
            net_r = raw_r - cost_r
            item = {
                "trade_id": self.trade_id,
                "engine_group": group,
                "engine": row["engine"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "side": row.get("side", ""),
                "entry_time": now,
                "exit_time": row["exit_time"],
                "nominal_risk_percent": nominal_risk,
                "pre_quality_risk_percent": pre_quality,
                "risk_percent": requested,
                "executed_risk_percent": approved,
                "risk_dollars": self.balance * approved / 100.0,
                "raw_r_multiple": raw_r,
                "cost_r": cost_r,
                "r_multiple": net_r,
                "cost_regime": decision.regime,
                "cost_regime_reason": decision.reason,
                "quality_reason": quality_reason,
                "admission_reason": "V14_16_COST_EFFICIENT_QUALITY_ALLOCATION",
            }
            self.trade_id += 1
            self.active.append(item)
            if group == "ICT":
                self.day.total_entries.append(now)
                self.day.entries[str(item["symbol"])].append(now)
            stressed = self.balance - sum(x["risk_dollars"] for x in self.active)
            self.stress_dd = max(
                self.stress_dd,
                (self.peak - stressed) / self.peak * 100.0,
            )

        self.close_due(pd.Timestamp.max.tz_localize("UTC"))
        summary = summarize(
            PORTFOLIO_GUARD.starting_balance,
            self.balance,
            self.max_dd,
            self.stress_dd,
            self.closed,
            self.skipped,
        )
        trades = pd.DataFrame(self.closed)
        summary["drawdown_governor"] = self.governor.__dict__
        summary["governor_interventions"] = len(self.governor_events)
        summary["cost_regime_counts"] = dict(
            Counter(trades.get("cost_regime", pd.Series(dtype=str)).astype(str))
        )
        summary["modeled_cost_dollars"] = (
            float((trades["risk_dollars"] * trades["cost_r"]).sum())
            if not trades.empty
            else 0.0
        )
        summary["quality_allocated_trades"] = int(
            (trades.get("cost_regime", pd.Series(dtype=str)) == "QUALITY_ALLOCATED").sum()
        )
        return summary, trades, pd.DataFrame(self.skipped)


def quality_attribution(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path)
    if frame.empty or "cost_regime" not in frame:
        return {}
    selected = frame[frame["cost_regime"] == "QUALITY_ALLOCATED"].copy()
    output: dict[str, Any] = {}
    for (symbol, mode, engine), group in selected.groupby(
        ["symbol", "engine_group", "engine"]
    ):
        wins = float(group.loc[group["pnl"] > 0, "pnl"].sum())
        losses = float(-group.loc[group["pnl"] < 0, "pnl"].sum())
        output[f"{symbol}/{mode}/{engine}"] = {
            "trades": int(len(group)),
            "net_profit": float(group["pnl"].sum()),
            "profit_factor": wins / losses if losses > 0 else None,
            "mean_net_r": float(group["r_multiple"].mean()),
            "maximum_requested_risk_percent": float(group["risk_percent"].max()),
        }
    return output


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# V14.16 Cost-Efficient Quality Allocation",
        "",
        "V14.16 preserves V14.15 signals and reasoning. It increases allocation only for frozen cost-resilient profiles that were already admitted at full strength. It does not override pressure, expectancy, probation, observation or drawdown reductions.",
        "",
        "## Exact ten-year comparison",
        "",
        "| Scenario | V14.15 net | V14.16 net | Improvement | PF | Closed DD | Stress DD | Trades | Quality trades |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in EXTENDED_COST_SCENARIOS:
        summary = payload["results"][f"{scenario}/v14_13"]["summary"]
        reference = V14_15_REFERENCE_NET[scenario]
        lines.append(
            f"| {scenario} | ${reference:,.2f} | ${summary['net_profit']:,.2f} | "
            f"${summary['net_profit'] - reference:,.2f} | {summary['profit_factor']:.4f} | "
            f"{summary['max_closed_drawdown_percent']:.4f}% | "
            f"{summary['stress_drawdown_percent']:.4f}% | {summary['closed_trades']} | "
            f"{summary.get('quality_allocated_trades', 0)} |"
        )

    lines += [
        "",
        "## Quality attribution",
        "",
        "The allocation target is capped at 0.80%. Live uplift is not automatic: it additionally requires mature positive broker-net evidence.",
        "",
    ]
    for scenario, items in payload["quality_attribution"].items():
        lines += [
            f"### {scenario}",
            "",
            "| Symbol/mode/engine | Trades | Net | PF | Mean net R |",
            "|---|---:|---:|---:|---:|",
        ]
        for key, stats in sorted(items.items()):
            lines.append(
                f"| {key} | {stats['trades']} | ${stats['net_profit']:,.2f} | "
                f"{float(stats['profit_factor'] or 0):.3f} | {stats['mean_net_r']:.4f} |"
            )
        lines.append("")

    lines += [
        "## Controls retained",
        "",
        "- 0.80% maximum single-trade risk.",
        "- 1.75% maximum open ICT risk.",
        "- 3.25% maximum combined open risk for every quality allocation.",
        "- 7.5/8.5/9.0/9.6% drawdown governor and 10% stress boundary.",
        "- Full V14.15 cost, dual-engine conflict, loss, staleness and reconciliation controls.",
        "",
        "Historical modeled results do not guarantee demo or live returns. The branch remains READ_ONLY/demo-forward only.",
    ]
    (OUT / "V14_16_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    base.OUT = OUT
    base.GEN = OUT / "generated_candidates"
    base.COST_SCENARIOS = EXTENDED_COST_SCENARIOS
    base.CostRegimeConfig = ExtendedCostRegimeConfig
    base.cost_regime_decision = unified_cost_reasoning_decision
    base.strict_profile_evidence = extended_profile_evidence
    base.CostRegimeReplay = QualityAllocationReplay
    base.main()

    source = OUT / "v14_13_results.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["model"] = "V14.16_COST_EFFICIENT_QUALITY_ALLOCATION"
    payload["quality_risk_ceiling_percent"] = QUALITY_RISK_PERCENT
    payload["quality_profiles_are_pre_entry"] = True
    payload["quality_attribution"] = {}
    payload["dual_mode_coverage"] = {}
    for scenario in EXTENDED_COST_SCENARIOS:
        ledger = OUT / "ledgers" / scenario / "v14_13_trades.csv"
        payload["quality_attribution"][scenario] = quality_attribution(ledger)
        payload["dual_mode_coverage"][scenario] = _coverage(ledger)

    target = OUT / "v14_16_results.json"
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    shutil.copy2(source, OUT / "v14_16_base_results.json")
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
