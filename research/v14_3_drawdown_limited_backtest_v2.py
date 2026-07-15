"""Admission-preserving drawdown governor replay for the five-symbol portfolio."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor
from mt5_ai_bridge.v14_3_profit_preserving_profile import PORTFOLIO_GUARD, scaled_risk_percent
from research.v14_3_five_symbol_ict_10y_backtest import (
    ALL_SYMBOLS,
    build_new_ict_candidates,
    install_all_symbol_ict_profile,
    mode_coverage,
    source_attribution,
)
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates, load_v12, summarize
from research.v14_3_profit_preserving_backtest import ResearchReplay


class AdmissionPreservingReplay(ResearchReplay):
    """Keep the original admission/cap path but reduce actual dollars at risk."""

    def __init__(self, v12: pd.DataFrame, ict: pd.DataFrame, governor: DrawdownGovernor) -> None:
        super().__init__(v12, ict)
        self.governor = governor
        self.governor_events: list[dict[str, Any]] = []

    def run(self):
        stream = [(row["entry_time"], "V12", row) for row in self.v12.to_dict("records")]
        stream += [(row["entry_time"], "ICT", row) for row in self.ict.to_dict("records")]
        stream.sort(key=lambda item: (item[0], 0 if item[1] == "V12" else 1))
        for now, group, row in stream:
            self.close_due(now)
            self.reset_day(now)
            total_open = sum(x["risk_percent"] for x in self.active)
            ict_open = sum(x["risk_percent"] for x in self.active if x["engine_group"] == "ICT")
            if group == "V12":
                requested = float(row["risk_percent"])
                reason = "V12_MASTER_DD_GOVERNED"
            else:
                blocked = self.reject_reason(row, now)
                if blocked:
                    self.skipped.append({**row, "skip_reason": blocked})
                    continue
                symbol = row["symbol"]
                pressure = (
                    self.day.global_consecutive_losses > 0
                    or self.day.loss_pressure[symbol] > 0
                    or self.day.daily_pnl[symbol] < 0
                )
                requested = scaled_risk_percent(symbol, row["setup"], self.dd(), pressure)
                if ict_open + requested > PORTFOLIO_GUARD.max_ict_open_risk_percent + 1e-12:
                    self.skipped.append({**row, "skip_reason": "ICT_OPEN_RISK_CAP"})
                    continue
                if total_open + requested > PORTFOLIO_GUARD.max_combined_open_risk_percent + 1e-12:
                    self.skipped.append({**row, "skip_reason": "COMBINED_OPEN_RISK_CAP"})
                    continue
                reason = "ICT_PROFIT_PRESERVING_DD_GOVERNED"

            current_dd = self.dd()
            approved = self.governor.apply(requested, current_dd)
            if approved <= 0.0:
                self.skipped.append({**row, "skip_reason": "DRAWDOWN_GOVERNOR_HARD_STOP"})
                continue
            if approved < requested - 1e-12:
                self.governor_events.append({
                    "entry_time": now,
                    "symbol": row["symbol"],
                    "engine": row["engine"],
                    "drawdown_percent": current_dd,
                    "requested_risk_percent": requested,
                    "approved_risk_percent": approved,
                    "multiplier": approved / requested if requested else 0.0,
                })
            item = {
                "trade_id": self.trade_id,
                "engine_group": group,
                "engine": row["engine"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "side": row.get("side", ""),
                "entry_time": now,
                "exit_time": row["exit_time"],
                "risk_percent": requested,
                "executed_risk_percent": approved,
                "risk_dollars": self.balance * approved / 100.0,
                "r_multiple": float(row["r_multiple"]),
                "admission_reason": reason,
            }
            self.trade_id += 1
            self.active.append(item)
            if group == "ICT":
                self.day.total_entries.append(now)
                self.day.entries[item["symbol"]].append(now)
            stressed = self.balance - sum(x["risk_dollars"] for x in self.active)
            self.stress_dd = max(self.stress_dd, (self.peak - stressed) / self.peak * 100.0)
        self.close_due(pd.Timestamp.max.tz_localize("UTC"))
        summary = summarize(PORTFOLIO_GUARD.starting_balance, self.balance, self.max_dd, self.stress_dd, self.closed, self.skipped)
        summary["drawdown_governor"] = self.governor.__dict__
        summary["governor_interventions"] = len(self.governor_events)
        return summary, pd.DataFrame(self.closed), pd.DataFrame(self.skipped)


def run_case(name: str, v12: pd.DataFrame, ict: pd.DataFrame, governor: DrawdownGovernor, out: Path) -> dict[str, Any]:
    current_summary, current_trades, current_skipped = ResearchReplay(v12, ict).run()
    replay = AdmissionPreservingReplay(v12, ict, governor)
    improved_summary, improved_trades, improved_skipped = replay.run()
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    current_trades.to_csv(folder / "current_trades.csv", index=False)
    improved_trades.to_csv(folder / "drawdown_limited_trades.csv", index=False)
    current_skipped.to_csv(folder / "current_skipped.csv", index=False)
    improved_skipped.to_csv(folder / "drawdown_limited_skipped.csv", index=False)
    pd.DataFrame(replay.governor_events).to_csv(folder / "governor_interventions.csv", index=False)
    result = {
        "case": name,
        "current": current_summary,
        "drawdown_limited": improved_summary,
        "difference": {
            "net_profit": improved_summary["net_profit"] - current_summary["net_profit"],
            "ending_balance": improved_summary["ending_balance"] - current_summary["ending_balance"],
            "profit_factor": improved_summary["profit_factor"] - current_summary["profit_factor"],
            "max_drawdown_points": improved_summary["max_closed_drawdown_percent"] - current_summary["max_closed_drawdown_percent"],
            "stress_drawdown_points": improved_summary["stress_drawdown_percent"] - current_summary["stress_drawdown_percent"],
            "closed_trades": improved_summary["closed_trades"] - current_summary["closed_trades"],
        },
        "mode_coverage": mode_coverage(v12, ict, improved_trades),
        "engine_attribution": source_attribution(improved_trades),
    }
    (folder / "comparison.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def write_report(results: dict[str, Any], governor: DrawdownGovernor, out: Path) -> None:
    lines = ["# V14.3 Admission-Preserving Drawdown Backtest", "", "The original candidate admission and portfolio-cap path is retained. Only actual dollars at risk are reduced near the historical drawdown ceiling.", ""]
    for name, result in results.items():
        current, improved, diff = result["current"], result["drawdown_limited"], result["difference"]
        lines += [f"## {name}", "", "| Metric | Current | Drawdown-limited | Change |", "|---|---:|---:|---:|", f"| Net profit | ${current['net_profit']:,.2f} | ${improved['net_profit']:,.2f} | ${diff['net_profit']:,.2f} |", f"| Ending balance | ${current['ending_balance']:,.2f} | ${improved['ending_balance']:,.2f} | ${diff['ending_balance']:,.2f} |", f"| Profit factor | {current['profit_factor']:.4f} | {improved['profit_factor']:.4f} | {diff['profit_factor']:.4f} |", f"| Max closed DD | {current['max_closed_drawdown_percent']:.4f}% | {improved['max_closed_drawdown_percent']:.4f}% | {diff['max_drawdown_points']:.4f} pp |", f"| Stress DD | {current['stress_drawdown_percent']:.4f}% | {improved['stress_drawdown_percent']:.4f}% | {diff['stress_drawdown_points']:.4f} pp |", f"| Closed trades | {current['closed_trades']} | {improved['closed_trades']} | {diff['closed_trades']} |", f"| Governor interventions | 0 | {improved['governor_interventions']} | {improved['governor_interventions']} |", "", "### Profit by symbol", "", "| Symbol | Trades | Net | PF |", "|---|---:|---:|---:|"]
        for symbol in ALL_SYMBOLS:
            stats = improved["by_symbol"].get(symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0})
            lines.append(f"| {symbol} | {stats['trades']} | ${stats['net']:,.2f} | {stats['profit_factor']:.3f} |")
        lines.append("")
    lines += ["Research/shadow only. Broker-native costs and forward validation remain required."]
    (out / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    install_all_symbol_ict_profile()
    v12 = apply_weak_symbol_profile(load_v12(args.v12_ledger))
    existing = load_ict_candidates(args.ict_source)
    new_ict, selection = build_new_ict_candidates(args.out)
    ict = pd.concat([existing, new_ict], ignore_index=True, sort=False).sort_values(["entry_time", "symbol", "engine"]).drop_duplicates(["entry_time", "exit_time", "symbol", "engine", "side"])
    latest = max(v12["exit_time"].max(), ict["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    governor = DrawdownGovernor()
    results = {
        "full_repository_history": run_case("full_repository_history", v12, ict, governor, args.out),
        "exact_10_year_window": run_case("exact_10_year_window", filter_window(v12, start, latest), filter_window(ict, start, latest), governor, args.out),
    }
    payload = {"governor": governor.__dict__, "selection": selection, "results": results}
    (args.out / "all_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_report(results, governor, args.out)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
