"""V14.5 protected forward-test replay overlay.

Adds profit lock, giveback stop, symbol quarantine, GBPJPY throttle,
consecutive-loss pause, trade clustering limits, and equity high-watermark stop
to the V14.3 ICT candidate stream.

Research-only: this script does not connect to MT5 and never submits orders.
It replays historical candidate outcomes and applies the new safety overlay.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ICT_TRADES = ROOT / "research" / "v14_3_under10_target_out" / "selected_under10_target_trades.csv"
DEFAULT_OUT = ROOT / "research" / "v14_5_protected_forward_model_out"


@dataclass(frozen=True)
class Config:
    starting_balance: float = 5000.0
    default_risk_percent: float = 0.15
    max_risk_percent: float = 0.25
    post_loss_risk_percent: float = 0.10
    gbpjpy_base_risk_percent: float = 0.05
    profit_lock_trigger_percent: float = 0.35
    profit_giveback_percent: float = 35.0
    equity_hwm_stop_percent: float = 0.50
    symbol_daily_loss_cap_percent: float = 0.50
    global_pause_after_consecutive_losses: int = 2
    global_pause_hours: float = 2.0
    global_stop_after_daily_losses: int = 3
    symbol_pause_after_consecutive_losses: int = 2
    symbol_rolling_loss_count: int = 2
    symbol_rolling_loss_hours: float = 4.0
    symbol_stop_after_daily_losses: int = 2
    max_new_trades_per_symbol_hour: int = 1
    max_new_trades_total_hour: int = 2
    max_simultaneous_open_trades: int = 2


@dataclass
class DayState:
    date: object | None = None
    day_start_equity: float = 0.0
    day_high_equity: float = 0.0
    daily_realized_pnl: float = 0.0
    peak_daily_realized_pnl: float = 0.0
    profit_lock_enabled: bool = False
    global_consecutive_losses: int = 0
    global_daily_losses: int = 0
    global_pause_until: pd.Timestamp | None = None
    global_stop_day: bool = False
    total_entries_hour: deque = field(default_factory=deque)
    symbol_entries_hour: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    symbol_daily_pnl: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    symbol_daily_losses: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    symbol_consecutive_losses: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    symbol_loss_times: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    symbol_block_rest_day: dict[str, bool] = field(default_factory=lambda: defaultdict(bool))
    symbol_cooldown_until: dict[str, pd.Timestamp] = field(default_factory=dict)


def _safe_json(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


class ProtectedReplay:
    def __init__(self, candidates: pd.DataFrame, cfg: Config) -> None:
        self.cfg = cfg
        self.candidates = candidates.copy()
        for col in ["entry_time", "exit_time"]:
            self.candidates[col] = pd.to_datetime(self.candidates[col])
        self.candidates = self.candidates.sort_values(["entry_time", "symbol", "setup"]).reset_index(drop=True)
        self.balance = cfg.starting_balance
        self.peak = cfg.starting_balance
        self.max_dd = 0.0
        self.active: list[dict] = []
        self.accepted: list[dict] = []
        self.skipped: list[dict] = []
        self.events: list[dict] = []
        self.day = DayState()
        self.next_trade_id = 0

    def drawdown(self) -> float:
        if self.peak <= 0:
            return 0.0
        return max(0.0, (self.peak - self.balance) / self.peak * 100.0)

    def reset_day_if_needed(self, ts: pd.Timestamp) -> None:
        day = pd.Timestamp(ts).date()
        if self.day.date != day:
            self.day = DayState(date=day, day_start_equity=self.balance, day_high_equity=self.balance)
            self.events.append({"time": pd.Timestamp(ts).isoformat(), "event": "DAY_RESET", "equity": self.balance, "date": str(day)})

    def _prune_hour_windows(self, now: pd.Timestamp) -> None:
        cutoff = now - pd.Timedelta(hours=1)
        while self.day.total_entries_hour and self.day.total_entries_hour[0] < cutoff:
            self.day.total_entries_hour.popleft()
        for queue in self.day.symbol_entries_hour.values():
            while queue and queue[0] < cutoff:
                queue.popleft()

    def close_due(self, now: pd.Timestamp) -> None:
        due = sorted([item for item in self.active if item["exit_time"] <= now], key=lambda item: item["exit_time"])
        for item in due:
            self.reset_day_if_needed(item["exit_time"])
            pnl = float(item["risk_dollars"]) * float(item["r"])
            self.balance += pnl
            self.peak = max(self.peak, self.balance)
            self.day.day_high_equity = max(self.day.day_high_equity, self.balance)
            self.day.daily_realized_pnl += pnl
            self.day.peak_daily_realized_pnl = max(self.day.peak_daily_realized_pnl, self.day.daily_realized_pnl)
            symbol = str(item["symbol"])
            self.day.symbol_daily_pnl[symbol] += pnl

            if pnl < 0:
                self.day.global_consecutive_losses += 1
                self.day.global_daily_losses += 1
                self.day.symbol_daily_losses[symbol] += 1
                self.day.symbol_consecutive_losses[symbol] += 1
                self.day.symbol_loss_times[symbol].append(item["exit_time"])
                cutoff = item["exit_time"] - pd.Timedelta(hours=self.cfg.symbol_rolling_loss_hours)
                while self.day.symbol_loss_times[symbol] and self.day.symbol_loss_times[symbol][0] < cutoff:
                    self.day.symbol_loss_times[symbol].popleft()

                if self.day.global_consecutive_losses >= self.cfg.global_pause_after_consecutive_losses:
                    pause_until = item["exit_time"] + pd.Timedelta(hours=self.cfg.global_pause_hours)
                    if self.day.global_pause_until is None or pause_until > self.day.global_pause_until:
                        self.day.global_pause_until = pause_until
                    self.events.append({"time": item["exit_time"].isoformat(), "event": "CONSECUTIVE_LOSS_PAUSE", "until": pause_until.isoformat()})
                if self.day.global_daily_losses >= self.cfg.global_stop_after_daily_losses:
                    self.day.global_stop_day = True
                    self.events.append({"time": item["exit_time"].isoformat(), "event": "GLOBAL_DAILY_LOSS_STOP", "losses": self.day.global_daily_losses})
                if self.day.symbol_consecutive_losses[symbol] >= self.cfg.symbol_pause_after_consecutive_losses:
                    self.day.symbol_block_rest_day[symbol] = True
                    self.events.append({"time": item["exit_time"].isoformat(), "event": "SYMBOL_CONSECUTIVE_LOSS_BLOCK", "symbol": symbol})
                if len(self.day.symbol_loss_times[symbol]) >= self.cfg.symbol_rolling_loss_count:
                    cooldown_until = item["exit_time"] + pd.Timedelta(hours=self.cfg.symbol_rolling_loss_hours)
                    self.day.symbol_cooldown_until[symbol] = cooldown_until
                    self.events.append({"time": item["exit_time"].isoformat(), "event": "SYMBOL_ROLLING_LOSS_COOLDOWN", "symbol": symbol, "until": cooldown_until.isoformat()})
                if self.day.symbol_daily_losses[symbol] >= self.cfg.symbol_stop_after_daily_losses:
                    self.day.symbol_block_rest_day[symbol] = True
                    self.events.append({"time": item["exit_time"].isoformat(), "event": "SYMBOL_DAILY_LOSS_BLOCK", "symbol": symbol})
            else:
                self.day.global_consecutive_losses = 0
                self.day.symbol_consecutive_losses[symbol] = 0

            symbol_loss_cap = -self.cfg.symbol_daily_loss_cap_percent / 100.0 * self.day.day_start_equity
            if self.day.symbol_daily_pnl[symbol] <= symbol_loss_cap:
                self.day.symbol_block_rest_day[symbol] = True
                self.events.append({"time": item["exit_time"].isoformat(), "event": "SYMBOL_DAILY_LOSS_CAP_BLOCK", "symbol": symbol})

            if not self.day.profit_lock_enabled:
                trigger = self.cfg.profit_lock_trigger_percent / 100.0 * self.day.day_start_equity
                if self.day.daily_realized_pnl >= trigger:
                    self.day.profit_lock_enabled = True
                    self.events.append({"time": item["exit_time"].isoformat(), "event": "PROFIT_LOCK_ENABLED", "daily_pnl": self.day.daily_realized_pnl})

            post_dd = self.drawdown()
            self.max_dd = max(self.max_dd, post_dd)
            item.update({
                "pnl": pnl,
                "post_exit_equity": self.balance,
                "post_exit_dd": post_dd,
                "daily_realized_pnl_after": self.day.daily_realized_pnl,
                "symbol_daily_pnl_after": self.day.symbol_daily_pnl[symbol],
            })
            self.accepted.append(item)
            self.active.remove(item)
            self.events.append({"time": item["exit_time"].isoformat(), "event": "EXIT", "trade_id": item["trade_id"], "symbol": symbol, "pnl": pnl, "equity": self.balance, "drawdown_percent": post_dd})

    def block_reason(self, row) -> str | None:
        now = pd.Timestamp(row.entry_time)
        symbol = str(row.symbol)
        self._prune_hour_windows(now)
        if self.day.global_stop_day:
            return "GLOBAL_DAILY_LOSS_STOP"
        if self.day.global_pause_until is not None and now < self.day.global_pause_until:
            return "GLOBAL_CONSECUTIVE_LOSS_PAUSE"
        if self.day.profit_lock_enabled and self.day.peak_daily_realized_pnl > 0:
            floor = self.day.peak_daily_realized_pnl * (1.0 - self.cfg.profit_giveback_percent / 100.0)
            if self.day.daily_realized_pnl < floor:
                return "PROFIT_GIVEBACK_STOP"
        if self.day.day_start_equity > 0:
            hwm_drop = (self.day.day_high_equity - self.balance) / self.day.day_start_equity * 100.0
            if hwm_drop >= self.cfg.equity_hwm_stop_percent:
                return "EQUITY_HWM_STOP"
        if self.day.symbol_block_rest_day[symbol]:
            return "SYMBOL_BLOCK_REST_DAY"
        cooldown_until = self.day.symbol_cooldown_until.get(symbol)
        if cooldown_until is not None and now < cooldown_until:
            return "SYMBOL_ROLLING_LOSS_COOLDOWN"
        if len(self.day.symbol_entries_hour[symbol]) >= self.cfg.max_new_trades_per_symbol_hour:
            return "TRADE_CLUSTER_SYMBOL_HOUR"
        if len(self.day.total_entries_hour) >= self.cfg.max_new_trades_total_hour:
            return "TRADE_CLUSTER_TOTAL_HOUR"
        if len(self.active) >= self.cfg.max_simultaneous_open_trades:
            return "MAX_SIMULTANEOUS_OPEN_TRADES"
        return None

    def risk_percent(self, row) -> float:
        symbol = str(row.symbol)
        risk = self.cfg.default_risk_percent
        stable_global = self.day.global_consecutive_losses == 0 and self.day.daily_realized_pnl > 0
        near_day_high = self.day.day_start_equity <= 0 or ((self.day.day_high_equity - self.balance) / self.day.day_start_equity * 100.0 <= 0.25)
        stable_symbol = self.day.symbol_daily_pnl[symbol] >= 0
        if stable_global and near_day_high and stable_symbol:
            risk = self.cfg.max_risk_percent
        if self.day.global_consecutive_losses >= 1 or self.day.symbol_consecutive_losses[symbol] >= 1:
            risk = min(risk, self.cfg.post_loss_risk_percent)
        if symbol == "GBPJPY":
            if self.day.symbol_daily_pnl[symbol] <= 0:
                risk = min(risk, self.cfg.gbpjpy_base_risk_percent)
            else:
                risk = min(risk, self.cfg.default_risk_percent)
        return risk

    def run(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
        for row in self.candidates.itertuples(index=False):
            self.close_due(pd.Timestamp(row.entry_time))
            self.reset_day_if_needed(pd.Timestamp(row.entry_time))
            reason = self.block_reason(row)
            if reason:
                self.skipped.append({**row._asdict(), "skip_reason": reason, "pre_equity": self.balance, "daily_pnl": self.day.daily_realized_pnl, "symbol_daily_pnl": self.day.symbol_daily_pnl[str(row.symbol)]})
                continue
            risk_pct = self.risk_percent(row)
            risk_dollars = self.balance * risk_pct / 100.0
            item = {**row._asdict(), "trade_id": self.next_trade_id, "pre_equity": self.balance, "risk_percent": risk_pct, "risk_dollars": risk_dollars, "risk_reason": "protected_overlay"}
            self.next_trade_id += 1
            self.active.append(item)
            self.day.total_entries_hour.append(pd.Timestamp(row.entry_time))
            self.day.symbol_entries_hour[str(row.symbol)].append(pd.Timestamp(row.entry_time))
            self.events.append({"time": pd.Timestamp(row.entry_time).isoformat(), "event": "ENTRY", "trade_id": item["trade_id"], "symbol": str(row.symbol), "risk_percent": risk_pct, "equity": self.balance})
        self.close_due(pd.Timestamp.max)

        trades = pd.DataFrame(self.accepted)
        skipped = pd.DataFrame(self.skipped)
        events = pd.DataFrame(self.events)
        gross_profit = float(trades.loc[trades["pnl"] > 0, "pnl"].sum()) if not trades.empty else 0.0
        gross_loss = float(-trades.loc[trades["pnl"] < 0, "pnl"].sum()) if not trades.empty else 0.0
        by_symbol = {}
        if not trades.empty:
            for symbol, group in trades.groupby("symbol"):
                pnl = group["pnl"].astype(float)
                by_symbol[symbol] = {
                    "trades": int(len(group)),
                    "net": float(pnl.sum()),
                    "wins": int((pnl > 0).sum()),
                    "losses": int((pnl < 0).sum()),
                    "profit_factor": float(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum()) if (pnl < 0).any() else None,
                }
        summary = {
            "config": asdict(self.cfg),
            "starting_balance": self.cfg.starting_balance,
            "ending_balance": self.balance,
            "net_profit": self.balance - self.cfg.starting_balance,
            "return_percent": (self.balance / self.cfg.starting_balance - 1.0) * 100.0,
            "closed_trades": int(len(trades)),
            "skipped_trades": int(len(skipped)),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "max_drawdown_percent": self.max_dd,
            "by_symbol": by_symbol,
            "skip_reasons": skipped["skip_reason"].value_counts().to_dict() if not skipped.empty else {},
            "risk_percent_counts": trades["risk_percent"].round(4).value_counts().sort_index().to_dict() if not trades.empty else {},
        }
        return trades, skipped, events, summary


def write_report(out: Path, summary: dict, source: Path, candidates: pd.DataFrame) -> None:
    start = pd.to_datetime(candidates["entry_time"]).min()
    end = pd.to_datetime(candidates["entry_time"]).max()
    report = f"""# V14.5 Protected Forward Model Backtest

## Purpose

This replay adds the requested profit-protection and symbol-quarantine layer after GBPJPY produced a loss cluster in demo forward testing. The entry model is unchanged; this layer only controls candidate admission and risk sizing.

## Data Used

- Candidate source: `{source}`
- Window: {start} to {end}
- Candidate count: {len(candidates):,}

## Protection Rules

- Default risk: {summary['config']['default_risk_percent']}%.
- Max forward-test risk: {summary['config']['max_risk_percent']}%.
- GBPJPY base risk: {summary['config']['gbpjpy_base_risk_percent']}% unless GBPJPY is positive for the day.
- Post-loss risk: {summary['config']['post_loss_risk_percent']}%.
- Profit lock trigger: +{summary['config']['profit_lock_trigger_percent']}% of day-start equity.
- Profit giveback stop: {summary['config']['profit_giveback_percent']}% of peak daily realized profit.
- Equity high-watermark stop: {summary['config']['equity_hwm_stop_percent']}% from day high.
- Symbol daily loss cap: {summary['config']['symbol_daily_loss_cap_percent']}% of day-start equity.
- Symbol block: after {summary['config']['symbol_pause_after_consecutive_losses']} consecutive losses or {summary['config']['symbol_stop_after_daily_losses']} daily losses.
- Global pause: {summary['config']['global_pause_hours']} hours after {summary['config']['global_pause_after_consecutive_losses']} consecutive losses.

## Result

| Metric | Result |
|---|---:|
| Starting balance | ${summary['starting_balance']:,.2f} |
| Ending balance | ${summary['ending_balance']:,.2f} |
| Net profit | ${summary['net_profit']:,.2f} |
| Return | {summary['return_percent']:.2f}% |
| Closed trades | {summary['closed_trades']:,} |
| Skipped candidates | {summary['skipped_trades']:,} |
| Profit factor | {summary['profit_factor']:.3f} |
| Max drawdown | {summary['max_drawdown_percent']:.2f}% |

## By Symbol

| Symbol | Trades | Net P/L | Wins | Losses | Profit Factor |
|---|---:|---:|---:|---:|---:|
"""
    for symbol, item in summary["by_symbol"].items():
        report += f"| {symbol} | {item['trades']:,} | ${item['net']:,.2f} | {item['wins']:,} | {item['losses']:,} | {item['profit_factor']:.3f} |\n"
    report += "\n## Skip Reasons\n\n| Reason | Count |\n|---|---:|\n"
    for reason, count in summary["skip_reasons"].items():
        report += f"| {reason} | {count:,} |\n"
    report += "\n## Interpretation\n\nThis overlay is more defensive than V14.3. It should reduce live/demo loss clustering, especially on GBPJPY, but it also reduces historical upside because many candidates are skipped or risk-throttled. Keep it in demo forward testing before any prop/live use.\n"
    (out / "V14_5_PROTECTED_FORWARD_MODEL_BACKTEST_REPORT.md").write_text(report, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay V14.3 ICT candidates with V14.5 profit and symbol protection overlay")
    parser.add_argument("--ict-trades", type=Path, default=DEFAULT_ICT_TRADES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.ict_trades.exists():
        raise FileNotFoundError(f"ICT trade candidate file not found: {args.ict_trades}")
    candidates = pd.read_csv(args.ict_trades)
    required = {"entry_time", "exit_time", "symbol", "setup", "r"}
    missing = required - set(candidates.columns)
    if missing:
        raise ValueError(f"candidate file missing required columns: {sorted(missing)}")

    args.out.mkdir(parents=True, exist_ok=True)
    replay = ProtectedReplay(candidates, Config())
    trades, skipped, events, summary = replay.run()
    trades.to_csv(args.out / "v14_5_protected_trades.csv", index=False)
    skipped.to_csv(args.out / "v14_5_protected_skipped.csv", index=False)
    events.to_csv(args.out / "v14_5_protected_events.csv", index=False)
    if not trades.empty:
        trades.assign(year=pd.to_datetime(trades["entry_time"]).dt.year).groupby("year").agg(
            trades=("trade_id", "count"),
            pnl=("pnl", "sum"),
            max_dd=("post_exit_dd", "max"),
        ).reset_index().to_csv(args.out / "v14_5_protected_by_year.csv", index=False)
        trades.groupby("symbol").agg(
            trades=("trade_id", "count"),
            pnl=("pnl", "sum"),
            avg_risk=("risk_percent", "mean"),
        ).reset_index().to_csv(args.out / "v14_5_protected_by_symbol.csv", index=False)
    (args.out / "v14_5_protected_summary.json").write_text(json.dumps(summary, indent=2, default=_safe_json), encoding="utf-8")
    write_report(args.out, summary, args.ict_trades, candidates)
    print(json.dumps(summary, indent=2, default=_safe_json))


if __name__ == "__main__":
    main()
