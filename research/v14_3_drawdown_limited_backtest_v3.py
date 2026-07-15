"""Fixed-admission risk-sizing replay for the five-symbol V14.3 portfolio.

The verified portfolio determines the accepted trade schedule first. The drawdown
layer then replays exactly those trades and changes only dollars at risk. This
isolates sizing effects from candidate-admission path changes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import apply_weak_symbol_profile
from mt5_ai_bridge.v14_3_drawdown_governor import DrawdownGovernor
from mt5_ai_bridge.v14_3_profit_preserving_profile import PORTFOLIO_GUARD
from research.v14_3_five_symbol_ict_10y_backtest import (
    ALL_SYMBOLS,
    build_new_ict_candidates,
    install_all_symbol_ict_profile,
    source_attribution,
)
from research.v14_3_production_improved_backtest import filter_window, load_ict_candidates, load_v12, summarize
from research.v14_3_profit_preserving_backtest import ResearchReplay


class FixedAdmissionRiskReplay:
    def __init__(self, admitted: pd.DataFrame, governor: DrawdownGovernor) -> None:
        self.admitted = admitted.copy()
        self.governor = governor
        self.balance = self.peak = PORTFOLIO_GUARD.starting_balance
        self.max_dd = self.stress_dd = 0.0
        self.active: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def dd(self) -> float:
        return max(0.0, (self.peak - self.balance) / self.peak * 100.0)

    def close_due(self, now: pd.Timestamp) -> None:
        due = sorted([item for item in self.active if item["exit_time"] <= now], key=lambda item: (item["exit_time"], item["trade_id"]))
        for item in due:
            pnl = item["risk_dollars"] * item["r_multiple"]
            self.balance += pnl
            self.peak = max(self.peak, self.balance)
            self.max_dd = max(self.max_dd, self.dd())
            self.active.remove(item)
            self.closed.append({**item, "pnl": pnl, "equity_after": self.balance, "drawdown_after": self.dd()})

    def run(self):
        rows = self.admitted.sort_values(["entry_time", "trade_id"]).to_dict("records")
        for row in rows:
            now = pd.Timestamp(row["entry_time"])
            self.close_due(now)
            requested = float(row["risk_percent"])
            approved = self.governor.apply(requested, self.dd())
            if approved <= 0.0:
                approved = self.governor.minimum_risk_percent
            if approved < requested - 1e-12:
                self.events.append({
                    "entry_time": now,
                    "trade_id": int(row["trade_id"]),
                    "symbol": row["symbol"],
                    "engine": row["engine"],
                    "drawdown_percent": self.dd(),
                    "requested_risk_percent": requested,
                    "approved_risk_percent": approved,
                    "multiplier": approved / requested if requested else 0.0,
                })
            item = {
                "trade_id": int(row["trade_id"]),
                "engine_group": row["engine_group"],
                "engine": row["engine"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "side": row.get("side", ""),
                "entry_time": now,
                "exit_time": pd.Timestamp(row["exit_time"]),
                "risk_percent": requested,
                "executed_risk_percent": approved,
                "risk_dollars": self.balance * approved / 100.0,
                "r_multiple": float(row["r_multiple"]),
                "admission_reason": "FIXED_VERIFIED_SCHEDULE_DD_GOVERNED",
            }
            self.active.append(item)
            stressed = self.balance - sum(x["risk_dollars"] for x in self.active)
            self.stress_dd = max(self.stress_dd, (self.peak - stressed) / self.peak * 100.0)
        self.close_due(pd.Timestamp.max.tz_localize("UTC"))
        summary = summarize(PORTFOLIO_GUARD.starting_balance, self.balance, self.max_dd, self.stress_dd, self.closed, [])
        summary["drawdown_governor"] = self.governor.__dict__
        summary["governor_interventions"] = len(self.events)
        summary["fixed_admission_schedule"] = True
        return summary, pd.DataFrame(self.closed), pd.DataFrame(self.events)


def run_case(name: str, v12: pd.DataFrame, ict: pd.DataFrame, governor: DrawdownGovernor, out: Path) -> dict[str, Any]:
    current_summary, current_trades, current_skipped = ResearchReplay(v12, ict).run()
    improved_summary, improved_trades, events = FixedAdmissionRiskReplay(current_trades, governor).run()
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    current_trades.to_csv(folder / "current_trades.csv", index=False)
    current_skipped.to_csv(folder / "current_skipped.csv", index=False)
    improved_trades.to_csv(folder / "drawdown_limited_trades.csv", index=False)
    events.to_csv(folder / "governor_interventions.csv", index=False)
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
        "engine_attribution": source_attribution(improved_trades),
    }
    (folder / "comparison.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def write_report(results: dict[str, Any], out: Path) -> None:
    lines = ["# V14.3 Fixed-Admission Drawdown Backtest", "", "The accepted trade schedule is frozen from the verified portfolio. Only dollars at risk are reduced near the drawdown ceiling.", ""]
    for name, result in results.items():
        c, i, d = result["current"], result["drawdown_limited"], result["difference"]
        lines += [f"## {name}", "", "| Metric | Current | Drawdown-limited | Change |", "|---|---:|---:|---:|", f"| Net profit | ${c['net_profit']:,.2f} | ${i['net_profit']:,.2f} | ${d['net_profit']:,.2f} |", f"| Ending balance | ${c['ending_balance']:,.2f} | ${i['ending_balance']:,.2f} | ${d['ending_balance']:,.2f} |", f"| Profit factor | {c['profit_factor']:.4f} | {i['profit_factor']:.4f} | {d['profit_factor']:.4f} |", f"| Max closed DD | {c['max_closed_drawdown_percent']:.4f}% | {i['max_closed_drawdown_percent']:.4f}% | {d['max_drawdown_points']:.4f} pp |", f"| Stress DD | {c['stress_drawdown_percent']:.4f}% | {i['stress_drawdown_percent']:.4f}% | {d['stress_drawdown_points']:.4f} pp |", f"| Closed trades | {c['closed_trades']} | {i['closed_trades']} | {d['closed_trades']} |", f"| Governor interventions | 0 | {i['governor_interventions']} | {i['governor_interventions']} |", "", "### Profit by symbol", "", "| Symbol | Trades | Net | PF |", "|---|---:|---:|---:|"]
        for symbol in ALL_SYMBOLS:
            stats = i["by_symbol"].get(symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0})
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
    write_report(results, args.out)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
