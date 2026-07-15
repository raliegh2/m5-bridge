"""Research-only chronological replay for the V12 + V14.3 signal ledgers.

This module does not connect to MT5 or place orders. It compares the committed
baseline with a frozen, pre-entry risk allocation profile.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from mt5_ai_bridge.v14_3_profit_preserving_profile import (
    PORTFOLIO_GUARD,
    SYMBOL_GUARDS,
    SETUP_RISK_PERCENT,
    scaled_risk_percent,
)
from research.v14_3_production_improved_backtest import (
    ALL_SYMBOLS,
    PortfolioPolicy,
    baseline_replay,
    diagnostics,
    filter_window,
    load_ict_candidates,
    load_v12,
    summarize,
)


@dataclass
class DayState:
    date: object | None = None
    start_equity: float = 0.0
    global_consecutive_losses: int = 0
    global_daily_losses: int = 0
    pause_until: pd.Timestamp | None = None
    stop_day: bool = False
    total_entries: deque = field(default_factory=deque)
    entries: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    daily_pnl: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    daily_losses: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    consecutive_losses: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    loss_pressure: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    loss_times: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    blocked: dict[str, bool] = field(default_factory=lambda: defaultdict(bool))
    cooldown_until: dict[str, pd.Timestamp] = field(default_factory=dict)


class ResearchReplay:
    def __init__(self, v12: pd.DataFrame, ict: pd.DataFrame) -> None:
        self.v12 = v12
        self.ict = ict
        self.balance = self.peak = PORTFOLIO_GUARD.starting_balance
        self.max_dd = self.stress_dd = 0.0
        self.active: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []
        self.day = DayState()
        self.trade_id = 1

    def dd(self) -> float:
        return max(0.0, (self.peak - self.balance) / self.peak * 100.0)

    def reset_day(self, now: pd.Timestamp) -> None:
        current = now.date()
        if self.day.date != current:
            self.day = DayState(date=current, start_equity=self.balance)

    def record_close(self, item: dict[str, Any], pnl: float, now: pd.Timestamp) -> None:
        if item["engine_group"] != "ICT":
            return
        self.reset_day(now)
        symbol = item["symbol"]
        guard = SYMBOL_GUARDS[symbol]
        self.day.daily_pnl[symbol] += pnl
        if pnl < 0:
            self.day.global_consecutive_losses += 1
            self.day.global_daily_losses += 1
            self.day.daily_losses[symbol] += 1
            self.day.consecutive_losses[symbol] += 1
            self.day.loss_pressure[symbol] += 1.0
            self.day.loss_times[symbol].append(now)
            cutoff = now - pd.Timedelta(hours=guard.rolling_loss_hours)
            while self.day.loss_times[symbol] and self.day.loss_times[symbol][0] < cutoff:
                self.day.loss_times[symbol].popleft()
            if self.day.global_consecutive_losses >= PORTFOLIO_GUARD.global_pause_after_consecutive_losses:
                self.day.pause_until = now + pd.Timedelta(hours=PORTFOLIO_GUARD.global_pause_hours)
            if self.day.global_daily_losses >= PORTFOLIO_GUARD.global_stop_after_daily_losses:
                self.day.stop_day = True
            if self.day.consecutive_losses[symbol] >= guard.block_after_consecutive_losses:
                self.day.blocked[symbol] = True
            if len(self.day.loss_times[symbol]) >= guard.rolling_loss_count:
                self.day.cooldown_until[symbol] = now + pd.Timedelta(hours=guard.rolling_loss_hours)
            if self.day.daily_losses[symbol] >= guard.stop_after_daily_losses:
                self.day.blocked[symbol] = True
        elif pnl > 0:
            self.day.global_consecutive_losses = 0
            self.day.consecutive_losses[symbol] = 0
            self.day.loss_pressure[symbol] = max(0.0, self.day.loss_pressure[symbol] - guard.win_pressure_recovery)
        limit = -guard.daily_loss_cap_percent / 100.0 * self.day.start_equity
        if self.day.daily_pnl[symbol] <= limit:
            self.day.blocked[symbol] = True

    def close_due(self, now: pd.Timestamp) -> None:
        due = sorted(
            [item for item in self.active if item["exit_time"] <= now],
            key=lambda item: (item["exit_time"], item["trade_id"]),
        )
        for item in due:
            pnl = item["risk_dollars"] * item["r_multiple"]
            self.balance += pnl
            self.peak = max(self.peak, self.balance)
            self.max_dd = max(self.max_dd, self.dd())
            self.active.remove(item)
            self.record_close(item, pnl, item["exit_time"])
            self.closed.append({**item, "pnl": pnl, "equity_after": self.balance, "drawdown_after": self.dd()})

    def prune(self, now: pd.Timestamp) -> None:
        cutoff = now - pd.Timedelta(hours=1)
        while self.day.total_entries and self.day.total_entries[0] < cutoff:
            self.day.total_entries.popleft()
        for queue in self.day.entries.values():
            while queue and queue[0] < cutoff:
                queue.popleft()

    def reject_reason(self, row: dict[str, Any], now: pd.Timestamp) -> str | None:
        symbol = row["symbol"]
        guard = SYMBOL_GUARDS[symbol]
        self.prune(now)
        if not (guard.session_start_hour_utc <= now.hour < guard.session_end_hour_utc):
            return "SYMBOL_SESSION_BLOCK"
        if self.dd() >= PORTFOLIO_GUARD.hard_drawdown_stop_percent:
            return "HARD_DRAWDOWN_STOP"
        if self.day.stop_day:
            return "GLOBAL_DAILY_LOSS_STOP"
        if self.day.pause_until is not None and now < self.day.pause_until:
            return "GLOBAL_CONSECUTIVE_LOSS_PAUSE"
        if self.day.blocked[symbol]:
            return "SYMBOL_BLOCK_REST_DAY"
        if symbol in self.day.cooldown_until and now < self.day.cooldown_until[symbol]:
            return "SYMBOL_ROLLING_LOSS_COOLDOWN"
        if len(self.day.entries[symbol]) >= guard.max_entries_per_hour:
            return "TRADE_CLUSTER_SYMBOL_HOUR"
        if len(self.day.total_entries) >= PORTFOLIO_GUARD.max_total_entries_per_hour:
            return "TRADE_CLUSTER_TOTAL_HOUR"
        open_symbol = sum(x["engine_group"] == "ICT" and x["symbol"] == symbol for x in self.active)
        if open_symbol >= guard.max_open_positions:
            return "SYMBOL_OPEN_POSITION_LIMIT"
        open_ict = sum(x["engine_group"] == "ICT" for x in self.active)
        if open_ict >= PORTFOLIO_GUARD.max_simultaneous_ict_positions:
            return "MAX_SIMULTANEOUS_ICT_POSITIONS"
        return None

    def run(self) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
        stream = [(row["entry_time"], "V12", row) for row in self.v12.to_dict("records")]
        stream += [(row["entry_time"], "ICT", row) for row in self.ict.to_dict("records")]
        stream.sort(key=lambda item: (item[0], 0 if item[1] == "V12" else 1))
        for now, group, row in stream:
            self.close_due(now)
            self.reset_day(now)
            total_open = sum(x["risk_percent"] for x in self.active)
            ict_open = sum(x["risk_percent"] for x in self.active if x["engine_group"] == "ICT")
            if group == "V12":
                risk = float(row["risk_percent"])
                reason = "V12_MASTER"
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
                risk = scaled_risk_percent(symbol, row["setup"], self.dd(), pressure)
                if ict_open + risk > PORTFOLIO_GUARD.max_ict_open_risk_percent + 1e-12:
                    self.skipped.append({**row, "skip_reason": "ICT_OPEN_RISK_CAP"})
                    continue
                if total_open + risk > PORTFOLIO_GUARD.max_combined_open_risk_percent + 1e-12:
                    self.skipped.append({**row, "skip_reason": "COMBINED_OPEN_RISK_CAP"})
                    continue
                reason = "ICT_PROFIT_PRESERVING"
            item = {
                "trade_id": self.trade_id,
                "engine_group": group,
                "engine": row["engine"],
                "symbol": row["symbol"],
                "setup": row["setup"],
                "side": row.get("side", ""),
                "entry_time": now,
                "exit_time": row["exit_time"],
                "risk_percent": risk,
                "risk_dollars": self.balance * risk / 100.0,
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
        summary = summarize(
            PORTFOLIO_GUARD.starting_balance,
            self.balance,
            self.max_dd,
            self.stress_dd,
            self.closed,
            self.skipped,
        )
        summary["profile"] = {
            "setup_risk_percent": {f"{s}/{p}": r for (s, p), r in SETUP_RISK_PERCENT.items()},
            "portfolio_guard": PORTFOLIO_GUARD.__dict__,
            "symbol_guards": {s: g.__dict__ for s, g in SYMBOL_GUARDS.items()},
        }
        return summary, pd.DataFrame(self.closed), pd.DataFrame(self.skipped)


def run_case(name: str, v12: pd.DataFrame, ict: pd.DataFrame, out: Path) -> dict[str, Any]:
    baseline, base_trades, base_skipped = baseline_replay(v12, ict, PortfolioPolicy())
    optimized, trades, skipped = ResearchReplay(v12, ict).run()
    folder = out / name
    folder.mkdir(parents=True, exist_ok=True)
    base_trades.to_csv(folder / "baseline_trades.csv", index=False)
    base_skipped.to_csv(folder / "baseline_skipped.csv", index=False)
    trades.to_csv(folder / "optimized_trades.csv", index=False)
    skipped.to_csv(folder / "optimized_skipped.csv", index=False)
    result = {
        "baseline": baseline,
        "optimized": optimized,
        "difference": {
            "net_profit": optimized["net_profit"] - baseline["net_profit"],
            "max_drawdown_points": optimized["max_closed_drawdown_percent"] - baseline["max_closed_drawdown_percent"],
        },
        "diagnostics": diagnostics(v12, ict, trades, skipped),
    }
    (folder / "comparison.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def report(results: dict[str, Any], out: Path) -> None:
    lines = ["# V14.3 Profit-Preserving Research Replay", ""]
    for name, result in results.items():
        b, o = result["baseline"], result["optimized"]
        lines.extend([
            f"## {name}", "",
            "| Metric | Baseline | Optimized |", "|---|---:|---:|",
            f"| Net profit | ${b['net_profit']:,.2f} | ${o['net_profit']:,.2f} |",
            f"| Ending balance | ${b['ending_balance']:,.2f} | ${o['ending_balance']:,.2f} |",
            f"| Profit factor | {b['profit_factor']:.3f} | {o['profit_factor']:.3f} |",
            f"| Max closed DD | {b['max_closed_drawdown_percent']:.2f}% | {o['max_closed_drawdown_percent']:.2f}% |",
            f"| Stress DD | {b['stress_drawdown_percent']:.2f}% | {o['stress_drawdown_percent']:.2f}% |",
            f"| Trades | {b['closed_trades']} | {o['closed_trades']} |", "",
            "### Optimized profit by symbol", "",
            "| Symbol | Trades | Net | PF |", "|---|---:|---:|---:|",
        ])
        for symbol in ALL_SYMBOLS:
            s = o["by_symbol"].get(symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0})
            lines.append(f"| {symbol} | {s['trades']} | ${s['net']:,.2f} | {s['profit_factor']:.3f} |")
        lines.extend(["", "### Rejections", "", "```json", json.dumps(o["skip_reasons"], indent=2), "```", ""])
    lines.append("Research/demo only. No broker connection or order placement is included.")
    (out / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot(results: dict[str, Any], out: Path) -> None:
    import matplotlib.pyplot as plt
    labels = list(results)
    b = [results[x]["baseline"]["net_profit"] for x in labels]
    o = [results[x]["optimized"]["net_profit"] for x in labels]
    pos = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar([x - 0.18 for x in pos], b, 0.36, label="Baseline")
    ax.bar([x + 0.18 for x in pos], o, 0.36, label="Optimized")
    ax.set_xticks(pos, labels)
    ax.set_ylabel("Net profit ($)")
    ax.set_title("V12 + V14.3 Profit-Preserving Replay")
    ax.legend()
    for container in ax.containers:
        ax.bar_label(container, fmt="$%.0f", padding=3)
    fig.tight_layout()
    fig.savefig(out / "profit_comparison.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    v12 = load_v12(args.v12_ledger)
    ict = load_ict_candidates(args.ict_source)
    latest = max(v12["exit_time"].max(), ict["exit_time"].max())
    start = latest - pd.DateOffset(years=10)
    results = {
        "full_repository_history": run_case("full_repository_history", v12, ict, args.out),
        "exact_10_year_window": run_case(
            "exact_10_year_window",
            filter_window(v12, start, latest),
            filter_window(ict, start, latest),
            args.out,
        ),
    }
    (args.out / "all_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    report(results, args.out)
    plot(results, args.out)
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
