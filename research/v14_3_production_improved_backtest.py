from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

ALL_SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
EXPECTED_ENGINES = {
    "GBPUSD": ("GBPUSD_V10_PRECISION", "GBPUSD_SWING_RETEST", "ICT_V14_3"),
    "EURUSD": ("EURUSD_SWING_CORE", "EURUSD_SWING_RETEST"),
    "GBPJPY": ("GBPJPY_SWING_CORE", "ICT_V14_3"),
    "AUDUSD": ("AUDUSD_TREND_PULLBACK",),
    "USDJPY": ("USDJPY_SAFE_HAVEN_BREAKOUT",),
}


def _ts(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _drawdown(peak: float, equity: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100.0)


def _profit_factor(values: pd.Series) -> float | None:
    if values.empty:
        return 0.0
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0)


@dataclass(frozen=True)
class SymbolPolicy:
    normal_risk: float
    post_loss_risk: float
    max_open_positions: int
    max_entries_hour: int
    daily_loss_cap_percent: float
    stop_after_daily_losses: int
    pause_after_consecutive_losses: int
    rolling_loss_count: int
    rolling_loss_hours: float
    session_start_hour_utc: int = 0
    session_end_hour_utc: int = 24
    win_pressure_recovery: float = 1.0


ICT_POLICIES = {
    "GBPUSD": SymbolPolicy(
        normal_risk=0.45,
        post_loss_risk=0.35,
        max_open_positions=4,
        max_entries_hour=3,
        daily_loss_cap_percent=2.0,
        stop_after_daily_losses=5,
        pause_after_consecutive_losses=4,
        rolling_loss_count=4,
        rolling_loss_hours=4.0,
    ),
    "GBPJPY": SymbolPolicy(
        normal_risk=0.20,
        post_loss_risk=0.10,
        max_open_positions=1,
        max_entries_hour=1,
        daily_loss_cap_percent=0.50,
        stop_after_daily_losses=2,
        pause_after_consecutive_losses=2,
        rolling_loss_count=2,
        rolling_loss_hours=4.0,
        session_start_hour_utc=7,
        session_end_hour_utc=20,
        win_pressure_recovery=0.5,
    ),
}


@dataclass(frozen=True)
class PortfolioPolicy:
    starting_balance: float = 5000.0
    ict_throttle_dd_percent: float = 8.0
    ict_throttle_risk_percent: float = 0.05
    ict_hard_dd_percent: float = 9.70
    max_ict_open_risk_percent: float = 1.25
    max_combined_open_risk_percent: float = 2.75
    max_simultaneous_ict_trades: int = 5
    max_total_entries_hour: int = 6
    global_pause_after_losses: int = 4
    global_pause_hours: float = 1.0
    global_stop_after_daily_losses: int = 8


@dataclass
class DayState:
    date: object | None = None
    day_start_equity: float = 0.0
    global_consecutive_losses: int = 0
    global_daily_losses: int = 0
    global_pause_until: pd.Timestamp | None = None
    global_stop_day: bool = False
    total_entries_hour: deque = field(default_factory=deque)
    symbol_entries_hour: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    symbol_daily_pnl: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    symbol_daily_losses: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    symbol_consecutive_losses: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    symbol_loss_pressure: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    symbol_loss_times: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    symbol_block_rest_day: dict[str, bool] = field(default_factory=lambda: defaultdict(bool))
    symbol_cooldown_until: dict[str, pd.Timestamp] = field(default_factory=dict)


def load_v12(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"entry_time", "exit_time", "symbol", "engine", "risk_percent", "r_multiple"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"V12 ledger missing: {sorted(missing)}")
    frame = frame.copy()
    frame["entry_time"] = frame["entry_time"].map(_ts)
    frame["exit_time"] = frame["exit_time"].map(_ts)
    frame["risk_percent"] = pd.to_numeric(frame["risk_percent"], errors="raise")
    frame["r_multiple"] = pd.to_numeric(frame["r_multiple"], errors="raise")
    frame["engine_group"] = "V12"
    if "setup" not in frame:
        frame["setup"] = "V12_FINAL"
    if "side" not in frame:
        frame["side"] = ""
    return frame.sort_values(["entry_time", "engine", "setup"]).reset_index(drop=True)


def load_ict_candidates(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "engine_group" in frame.columns:
        frame = frame[frame["engine_group"].astype(str).str.upper().eq("ICT")].copy()
    required = {"entry_time", "exit_time", "symbol", "setup"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ICT source missing: {sorted(missing)}")
    if "r_multiple" not in frame and "r" in frame:
        frame["r_multiple"] = frame["r"]
    if "r_multiple" not in frame:
        raise ValueError("ICT source requires r_multiple or r")
    frame = frame.copy()
    frame["entry_time"] = frame["entry_time"].map(_ts)
    frame["exit_time"] = frame["exit_time"].map(_ts)
    frame["r_multiple"] = pd.to_numeric(frame["r_multiple"], errors="raise")
    frame["engine"] = "ICT_V14_3"
    if "side" not in frame:
        frame["side"] = frame.get("direction", "")
    keep = ["entry_time", "exit_time", "symbol", "setup", "side", "r_multiple", "engine"]
    frame = frame[keep].drop_duplicates(["entry_time", "exit_time", "symbol", "setup", "side"])
    return frame.sort_values(["entry_time", "symbol", "setup"]).reset_index(drop=True)


def filter_window(frame: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None) -> pd.DataFrame:
    output = frame
    if start is not None:
        output = output[output["entry_time"] >= start]
    if end is not None:
        output = output[output["entry_time"] <= end]
    return output.reset_index(drop=True)


def summarize(starting_balance: float, ending_balance: float, max_dd: float, stress_dd: float, closed: list[dict], skipped: list[dict]) -> dict:
    trades = pd.DataFrame(closed)
    rejected = pd.DataFrame(skipped)
    pnl = trades["pnl"].astype(float) if not trades.empty else pd.Series(dtype=float)
    by_symbol: dict[str, dict] = {}
    by_engine: dict[str, dict] = {}
    by_group: dict[str, dict] = {}
    for column, target in (("symbol", by_symbol), ("engine", by_engine), ("engine_group", by_group)):
        if trades.empty:
            continue
        for name, group in trades.groupby(column):
            values = group["pnl"].astype(float)
            target[str(name)] = {
                "trades": int(len(group)),
                "net": float(values.sum()),
                "profit_factor": _profit_factor(values),
                "wins": int((values > 0).sum()),
                "losses": int((values < 0).sum()),
            }
    return {
        "starting_balance": starting_balance,
        "ending_balance": ending_balance,
        "net_profit": ending_balance - starting_balance,
        "return_percent": (ending_balance / starting_balance - 1.0) * 100.0,
        "closed_trades": int(len(trades)),
        "skipped_ict_trades": int(len(rejected)),
        "profit_factor": _profit_factor(pnl),
        "max_closed_drawdown_percent": max_dd,
        "stress_drawdown_percent": stress_dd,
        "by_symbol": by_symbol,
        "by_engine": by_engine,
        "by_engine_group": by_group,
        "skip_reasons": rejected["skip_reason"].value_counts().to_dict() if not rejected.empty else {},
    }


def baseline_replay(v12: pd.DataFrame, ict: pd.DataFrame, policy: PortfolioPolicy) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    balance = peak = policy.starting_balance
    max_closed_dd = stress_dd = 0.0
    active: list[dict] = []
    closed: list[dict] = []
    skipped: list[dict] = []
    stream = [(row["entry_time"], "V12", row) for row in v12.to_dict("records")]
    stream += [(row["entry_time"], "ICT", row) for row in ict.to_dict("records")]
    stream.sort(key=lambda item: (item[0], 0 if item[1] == "V12" else 1))
    trade_id = 1

    def close_due(now: pd.Timestamp) -> None:
        nonlocal balance, peak, max_closed_dd
        due = sorted([position for position in active if position["exit_time"] <= now], key=lambda position: (position["exit_time"], position["trade_id"]))
        for item in due:
            pnl = item["risk_dollars"] * item["r_multiple"]
            balance += pnl
            peak = max(peak, balance)
            max_closed_dd = max(max_closed_dd, _drawdown(peak, balance))
            active.remove(item)
            closed.append({**item, "pnl": pnl, "equity_after": balance, "drawdown_after": _drawdown(peak, balance)})

    for now, group, row in stream:
        close_due(now)
        pre_dd = _drawdown(peak, balance)
        total_open = sum(position["risk_percent"] for position in active)
        ict_open = sum(position["risk_percent"] for position in active if position["engine_group"] == "ICT")
        if group == "V12":
            risk = float(row["risk_percent"])
            reason = "V12_MASTER"
        else:
            if pre_dd >= policy.ict_hard_dd_percent:
                skipped.append({**row, "skip_reason": "ICT_HARD_DD"})
                continue
            risk = policy.ict_throttle_risk_percent if pre_dd >= policy.ict_throttle_dd_percent else 0.45
            reason = "ICT_DD_THROTTLE" if pre_dd >= policy.ict_throttle_dd_percent else "ICT_ACTIVE"
            if ict_open + risk > policy.max_ict_open_risk_percent + 1e-12:
                skipped.append({**row, "skip_reason": "ICT_OPEN_RISK_CAP"})
                continue
            if total_open + risk > policy.max_combined_open_risk_percent + 1e-12:
                skipped.append({**row, "skip_reason": "COMBINED_OPEN_RISK_CAP"})
                continue
        item = {
            "trade_id": trade_id,
            "engine_group": group,
            "engine": row["engine"],
            "symbol": row["symbol"],
            "setup": row["setup"],
            "side": row.get("side", ""),
            "entry_time": now,
            "exit_time": row["exit_time"],
            "risk_percent": risk,
            "risk_dollars": balance * risk / 100.0,
            "r_multiple": float(row["r_multiple"]),
            "admission_reason": reason,
        }
        trade_id += 1
        active.append(item)
        stressed = balance - sum(position["risk_dollars"] for position in active)
        stress_dd = max(stress_dd, _drawdown(peak, stressed))
    close_due(pd.Timestamp.max.tz_localize("UTC"))
    return summarize(policy.starting_balance, balance, max_closed_dd, stress_dd, closed, skipped), pd.DataFrame(closed), pd.DataFrame(skipped)


class ImprovedReplay:
    def __init__(self, v12: pd.DataFrame, ict: pd.DataFrame, policy: PortfolioPolicy):
        self.v12 = v12
        self.ict = ict
        self.policy = policy
        self.balance = self.peak = policy.starting_balance
        self.max_closed_dd = self.stress_dd = 0.0
        self.active: list[dict] = []
        self.closed: list[dict] = []
        self.skipped: list[dict] = []
        self.day = DayState()
        self.trade_id = 1

    def reset_day(self, now: pd.Timestamp) -> None:
        date = now.date()
        if self.day.date != date:
            self.day = DayState(date=date, day_start_equity=self.balance)

    def close_due(self, now: pd.Timestamp) -> None:
        due = sorted([position for position in self.active if position["exit_time"] <= now], key=lambda position: (position["exit_time"], position["trade_id"]))
        for item in due:
            pnl = item["risk_dollars"] * item["r_multiple"]
            self.balance += pnl
            self.peak = max(self.peak, self.balance)
            self.max_closed_dd = max(self.max_closed_dd, _drawdown(self.peak, self.balance))
            self.active.remove(item)
            if item["engine_group"] == "ICT":
                self.record_ict_close(item, pnl, item["exit_time"])
            self.closed.append({**item, "pnl": pnl, "equity_after": self.balance, "drawdown_after": _drawdown(self.peak, self.balance)})

    def record_ict_close(self, item: dict, pnl: float, now: pd.Timestamp) -> None:
        self.reset_day(now)
        symbol = item["symbol"]
        symbol_policy = ICT_POLICIES[symbol]
        self.day.symbol_daily_pnl[symbol] += pnl
        if pnl < 0:
            self.day.global_consecutive_losses += 1
            self.day.global_daily_losses += 1
            self.day.symbol_daily_losses[symbol] += 1
            self.day.symbol_consecutive_losses[symbol] += 1
            self.day.symbol_loss_pressure[symbol] += 1.0
            self.day.symbol_loss_times[symbol].append(now)
            cutoff = now - pd.Timedelta(hours=symbol_policy.rolling_loss_hours)
            while self.day.symbol_loss_times[symbol] and self.day.symbol_loss_times[symbol][0] < cutoff:
                self.day.symbol_loss_times[symbol].popleft()
            if self.day.global_consecutive_losses >= self.policy.global_pause_after_losses:
                self.day.global_pause_until = now + pd.Timedelta(hours=self.policy.global_pause_hours)
            if self.day.global_daily_losses >= self.policy.global_stop_after_daily_losses:
                self.day.global_stop_day = True
            if self.day.symbol_consecutive_losses[symbol] >= symbol_policy.pause_after_consecutive_losses:
                self.day.symbol_block_rest_day[symbol] = True
            if len(self.day.symbol_loss_times[symbol]) >= symbol_policy.rolling_loss_count:
                self.day.symbol_cooldown_until[symbol] = now + pd.Timedelta(hours=symbol_policy.rolling_loss_hours)
            if self.day.symbol_daily_losses[symbol] >= symbol_policy.stop_after_daily_losses:
                self.day.symbol_block_rest_day[symbol] = True
        elif pnl > 0:
            self.day.global_consecutive_losses = 0
            self.day.symbol_consecutive_losses[symbol] = 0
            self.day.symbol_loss_pressure[symbol] = max(0.0, self.day.symbol_loss_pressure[symbol] - symbol_policy.win_pressure_recovery)
        loss_cap = -symbol_policy.daily_loss_cap_percent / 100.0 * self.day.day_start_equity
        if self.day.symbol_daily_pnl[symbol] <= loss_cap:
            self.day.symbol_block_rest_day[symbol] = True

    def prune_hour(self, now: pd.Timestamp) -> None:
        cutoff = now - pd.Timedelta(hours=1)
        while self.day.total_entries_hour and self.day.total_entries_hour[0] < cutoff:
            self.day.total_entries_hour.popleft()
        for queue in self.day.symbol_entries_hour.values():
            while queue and queue[0] < cutoff:
                queue.popleft()

    def block_reason(self, row: dict, now: pd.Timestamp) -> str | None:
        symbol = row["symbol"]
        symbol_policy = ICT_POLICIES[symbol]
        self.prune_hour(now)
        if not (symbol_policy.session_start_hour_utc <= now.hour < symbol_policy.session_end_hour_utc):
            return "SYMBOL_SESSION_BLOCK"
        if self.day.global_stop_day:
            return "GLOBAL_DAILY_LOSS_STOP"
        if self.day.global_pause_until is not None and now < self.day.global_pause_until:
            return "GLOBAL_CONSECUTIVE_LOSS_PAUSE"
        if self.day.symbol_block_rest_day[symbol]:
            return "SYMBOL_BLOCK_REST_DAY"
        if symbol in self.day.symbol_cooldown_until and now < self.day.symbol_cooldown_until[symbol]:
            return "SYMBOL_ROLLING_LOSS_COOLDOWN"
        if len(self.day.symbol_entries_hour[symbol]) >= symbol_policy.max_entries_hour:
            return "TRADE_CLUSTER_SYMBOL_HOUR"
        if len(self.day.total_entries_hour) >= self.policy.max_total_entries_hour:
            return "TRADE_CLUSTER_TOTAL_HOUR"
        open_symbol = sum(position["engine_group"] == "ICT" and position["symbol"] == symbol for position in self.active)
        if open_symbol >= symbol_policy.max_open_positions:
            return "SYMBOL_OPEN_POSITION_LIMIT"
        if sum(position["engine_group"] == "ICT" for position in self.active) >= self.policy.max_simultaneous_ict_trades:
            return "MAX_SIMULTANEOUS_ICT_TRADES"
        return None

    def ict_risk(self, symbol: str, pre_dd: float) -> float:
        symbol_policy = ICT_POLICIES[symbol]
        reduced = self.day.global_consecutive_losses > 0 or self.day.symbol_loss_pressure[symbol] > 0 or self.day.symbol_daily_pnl[symbol] < 0
        risk = symbol_policy.post_loss_risk if reduced else symbol_policy.normal_risk
        if pre_dd >= self.policy.ict_throttle_dd_percent:
            risk = min(risk, self.policy.ict_throttle_risk_percent)
        return risk

    def run(self) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
        stream = [(row["entry_time"], "V12", row) for row in self.v12.to_dict("records")]
        stream += [(row["entry_time"], "ICT", row) for row in self.ict.to_dict("records")]
        stream.sort(key=lambda item: (item[0], 0 if item[1] == "V12" else 1))
        for now, group, row in stream:
            self.close_due(now)
            self.reset_day(now)
            pre_dd = _drawdown(self.peak, self.balance)
            total_open = sum(position["risk_percent"] for position in self.active)
            ict_open = sum(position["risk_percent"] for position in self.active if position["engine_group"] == "ICT")
            if group == "V12":
                risk = float(row["risk_percent"])
                reason = "V12_MASTER"
            else:
                if pre_dd >= self.policy.ict_hard_dd_percent:
                    self.skipped.append({**row, "skip_reason": "ICT_HARD_DD"})
                    continue
                reason = self.block_reason(row, now)
                if reason:
                    self.skipped.append({**row, "skip_reason": reason})
                    continue
                risk = self.ict_risk(row["symbol"], pre_dd)
                if ict_open + risk > self.policy.max_ict_open_risk_percent + 1e-12:
                    self.skipped.append({**row, "skip_reason": "ICT_OPEN_RISK_CAP"})
                    continue
                if total_open + risk > self.policy.max_combined_open_risk_percent + 1e-12:
                    self.skipped.append({**row, "skip_reason": "COMBINED_OPEN_RISK_CAP"})
                    continue
                reason = "ICT_SYMBOL_POLICY"
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
                self.day.total_entries_hour.append(now)
                self.day.symbol_entries_hour[row["symbol"]].append(now)
            stressed = self.balance - sum(position["risk_dollars"] for position in self.active)
            self.stress_dd = max(self.stress_dd, _drawdown(self.peak, stressed))
        self.close_due(pd.Timestamp.max.tz_localize("UTC"))
        return summarize(self.policy.starting_balance, self.balance, self.max_closed_dd, self.stress_dd, self.closed, self.skipped), pd.DataFrame(self.closed), pd.DataFrame(self.skipped)


def diagnostics(v12: pd.DataFrame, ict: pd.DataFrame, trades: pd.DataFrame, skipped: pd.DataFrame) -> dict:
    result: dict[str, dict] = {}
    for symbol in ALL_SYMBOLS:
        v12_rows = v12[v12["symbol"] == symbol]
        ict_rows = ict[ict["symbol"] == symbol]
        accepted = trades[trades["symbol"] == symbol] if not trades.empty else pd.DataFrame()
        rejected = skipped[skipped["symbol"] == symbol] if not skipped.empty and "symbol" in skipped else pd.DataFrame()
        risks = accepted[accepted["engine_group"] == "ICT"]["risk_percent"] if not accepted.empty else pd.Series(dtype=float)
        starts = [value for value in (v12_rows["entry_time"].min() if len(v12_rows) else None, ict_rows["entry_time"].min() if len(ict_rows) else None) if value is not None]
        ends = [value for value in (v12_rows["exit_time"].max() if len(v12_rows) else None, ict_rows["exit_time"].max() if len(ict_rows) else None) if value is not None]
        result[symbol] = {
            "data_available": bool(starts),
            "data_start": min(starts).isoformat() if starts else None,
            "data_end": max(ends).isoformat() if ends else None,
            "engines_expected": list(EXPECTED_ENGINES[symbol]),
            "v12_candidates_generated": int(len(v12_rows)),
            "ict_candidates_generated": int(len(ict_rows)),
            "accepted_trades": int(len(accepted)),
            "rejected_trades": int(len(rejected)),
            "rejection_codes": rejected["skip_reason"].value_counts().to_dict() if not rejected.empty else {},
            "ict_risk_min": float(risks.min()) if len(risks) else None,
            "ict_risk_max": float(risks.max()) if len(risks) else None,
            "current_spread": "NOT_AVAILABLE_IN_R_MULTIPLE_REPLAY",
            "next_eligible_scan": "LIVE_RUNNER_COMPLETED_CANDLE_SCHEDULE",
        }
    return result


def run_case(name: str, v12: pd.DataFrame, ict: pd.DataFrame, output: Path, policy: PortfolioPolicy) -> dict:
    baseline_summary, baseline_trades, baseline_skipped = baseline_replay(v12, ict, policy)
    improved_summary, improved_trades, improved_skipped = ImprovedReplay(v12, ict, policy).run()
    case_dir = output / name
    case_dir.mkdir(parents=True, exist_ok=True)
    baseline_trades.to_csv(case_dir / "baseline_trades.csv", index=False)
    baseline_skipped.to_csv(case_dir / "baseline_skipped.csv", index=False)
    improved_trades.to_csv(case_dir / "improved_trades.csv", index=False)
    improved_skipped.to_csv(case_dir / "improved_skipped.csv", index=False)
    comparison = {
        "case": name,
        "baseline": baseline_summary,
        "improved": improved_summary,
        "difference": {
            "net_profit": improved_summary["net_profit"] - baseline_summary["net_profit"],
            "profit_retained_percent": improved_summary["net_profit"] / baseline_summary["net_profit"] * 100.0 if baseline_summary["net_profit"] else None,
            "max_drawdown_points": improved_summary["max_closed_drawdown_percent"] - baseline_summary["max_closed_drawdown_percent"],
            "stress_drawdown_points": improved_summary["stress_drawdown_percent"] - baseline_summary["stress_drawdown_percent"],
            "trades": improved_summary["closed_trades"] - baseline_summary["closed_trades"],
        },
        "diagnostics": diagnostics(v12, ict, improved_trades, improved_skipped),
    }
    (case_dir / "comparison.json").write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
    return comparison


def write_report(results: dict, output: Path) -> None:
    lines = ["# V12 + V14.3 Production Improvement Backtest", ""]
    for name, result in results.items():
        baseline = result["baseline"]
        improved = result["improved"]
        difference = result["difference"]
        lines.extend([
            f"## {name}",
            "",
            "| Metric | Baseline | Improved | Change |",
            "|---|---:|---:|---:|",
            f"| Net profit | ${baseline['net_profit']:,.2f} | ${improved['net_profit']:,.2f} | ${difference['net_profit']:,.2f} |",
            f"| Ending balance | ${baseline['ending_balance']:,.2f} | ${improved['ending_balance']:,.2f} | ${improved['ending_balance']-baseline['ending_balance']:,.2f} |",
            f"| Profit factor | {baseline['profit_factor']:.3f} | {improved['profit_factor']:.3f} | {improved['profit_factor']-baseline['profit_factor']:.3f} |",
            f"| Max closed DD | {baseline['max_closed_drawdown_percent']:.2f}% | {improved['max_closed_drawdown_percent']:.2f}% | {difference['max_drawdown_points']:.2f} pp |",
            f"| Stress DD | {baseline['stress_drawdown_percent']:.2f}% | {improved['stress_drawdown_percent']:.2f}% | {difference['stress_drawdown_points']:.2f} pp |",
            f"| Closed trades | {baseline['closed_trades']} | {improved['closed_trades']} | {difference['trades']} |",
            f"| Profit retained | - | {difference['profit_retained_percent']:.2f}% | - |",
            "",
            "### Improved profit by symbol",
            "",
            "| Symbol | Trades | Net | PF |",
            "|---|---:|---:|---:|",
        ])
        for symbol in ALL_SYMBOLS:
            stats = improved["by_symbol"].get(symbol, {"trades": 0, "net": 0.0, "profit_factor": 0.0})
            lines.append(f"| {symbol} | {stats['trades']} | ${stats['net']:,.2f} | {stats['profit_factor']:.3f} |")
        lines.extend(["", "### Rejection reasons", "", "```json", json.dumps(improved["skip_reasons"], indent=2), "```", ""])
    (output / "BACKTEST_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_results(results: dict, output: Path) -> None:
    import matplotlib.pyplot as plt

    labels = list(results)
    baseline = [results[name]["baseline"]["net_profit"] for name in labels]
    improved = [results[name]["improved"]["net_profit"] for name in labels]
    positions = list(range(len(labels)))
    width = 0.35
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.bar([position - width / 2 for position in positions], baseline, width, label="Baseline")
    axis.bar([position + width / 2 for position in positions], improved, width, label="Improved")
    axis.set_ylabel("Net profit ($)")
    axis.set_title("V12 + V14.3 Profit Comparison")
    axis.set_xticks(positions, labels)
    axis.legend()
    axis.bar_label(axis.containers[0], fmt="$%.0f", padding=3)
    axis.bar_label(axis.containers[1], fmt="$%.0f", padding=3)
    figure.tight_layout()
    figure.savefig(output / "profit_comparison.png", dpi=160)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare the current V12 + V14.3 replay with production symbol safeguards")
    parser.add_argument("--v12-ledger", type=Path, required=True)
    parser.add_argument("--ict-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    v12 = load_v12(args.v12_ledger)
    ict = load_ict_candidates(args.ict_source)
    latest = max(v12["exit_time"].max(), ict["exit_time"].max())
    ten_year_start = latest - pd.DateOffset(years=10)
    policy = PortfolioPolicy()
    results = {
        "full_repository_history": run_case("full_repository_history", v12, ict, args.out, policy),
        "exact_10_year_window": run_case("exact_10_year_window", filter_window(v12, ten_year_start, latest), filter_window(ict, ten_year_start, latest), args.out, policy),
    }
    (args.out / "all_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    write_report(results, args.out)
    plot_results(results, args.out)
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
