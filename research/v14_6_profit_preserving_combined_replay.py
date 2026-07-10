"""V14.6 profit-preserving combined V12 + ICT replay.

This is a less defensive successor to V14.5. V14.5 protected the account but
cut too much upside. V14.6 keeps the V12 final ledger intact and applies a
profit-preserving protection overlay to the ICT satellite only:

- restores ICT active risk closer to V14.3;
- throttles GBPJPY instead of fully suppressing the whole model;
- uses wider profit-lock / high-watermark thresholds;
- only pauses globally after larger loss clusters;
- keeps V12 final contribution unchanged.

Research-only: no MT5 connection and no orders.
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
DEFAULT_V12 = ROOT / "research" / "v12_final_ledger_output" / "v12_final_trade_ledger.csv"
DEFAULT_ICT = ROOT / "research" / "v14_3_under10_target_out" / "selected_under10_target_trades.csv"
DEFAULT_OUT = ROOT / "research" / "v14_6_profit_preserving_combined_out"


@dataclass(frozen=True)
class V146Config:
    starting_balance: float = 5000.0
    ict_default_risk_percent: float = 0.45
    ict_max_risk_percent: float = 0.45
    ict_post_loss_risk_percent: float = 0.35
    gbpjpy_negative_day_risk_percent: float = 0.25
    gbpjpy_positive_day_risk_percent: float = 0.35
    profit_lock_trigger_percent: float = 2.0
    profit_giveback_percent: float = 75.0
    equity_hwm_stop_percent: float = 3.0
    symbol_daily_loss_cap_percent: float = 2.0
    global_pause_after_consecutive_losses: int = 4
    global_pause_hours: float = 1.0
    global_stop_after_daily_losses: int = 8
    symbol_pause_after_consecutive_losses: int = 4
    symbol_rolling_loss_count: int = 4
    symbol_rolling_loss_hours: float = 4.0
    symbol_stop_after_daily_losses: int = 5
    max_new_trades_per_symbol_hour: int = 3
    max_new_trades_total_hour: int = 6
    max_simultaneous_ict_trades: int = 5


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


def safe_json(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def load_v12(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"V12 ledger not found: {path}")
    df = pd.read_csv(path)
    for col in ["entry_time", "exit_time"]:
        df[col] = pd.to_datetime(df[col])
    df["engine_group"] = "V12"
    df["candidate_id"] = "V12_" + df["trade_id"].astype(str)
    df["r"] = pd.to_numeric(df["r_multiple"], errors="coerce").fillna(0.0)
    df["fixed_pnl"] = pd.to_numeric(df["pnl"], errors="coerce").fillna(0.0)
    df["risk_percent"] = pd.to_numeric(df["risk_percent"], errors="coerce").fillna(0.0)
    return df


def load_ict(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"ICT trades not found: {path}")
    df = pd.read_csv(path)
    for col in ["entry_time", "exit_time"]:
        df[col] = pd.to_datetime(df[col])
    df["engine_group"] = "ICT"
    df["candidate_id"] = "ICT_" + df["trade_id"].astype(str)
    df["r"] = pd.to_numeric(df["r"], errors="coerce").fillna(0.0)
    return df


class CombinedReplay:
    def __init__(self, v12: pd.DataFrame, ict: pd.DataFrame, cfg: V146Config) -> None:
        self.v12 = v12.copy()
        self.ict = ict.copy()
        self.cfg = cfg
        self.balance = cfg.starting_balance
        self.peak = cfg.starting_balance
        self.max_dd = 0.0
        self.day = DayState()
        self.open_ict: list[dict] = []
        self.trades: list[dict] = []
        self.skipped_ict: list[dict] = []
        self.events: list[dict] = []
        self.ict_entries = self.ict.to_dict("records")
        self.v12_entries = self.v12.to_dict("records")

    def reset_day(self, ts: pd.Timestamp) -> None:
        d = pd.Timestamp(ts).date()
        if self.day.date != d:
            self.day = DayState(date=d, day_start_equity=self.balance, day_high_equity=self.balance)
            self.events.append({"time": pd.Timestamp(ts).isoformat(), "event": "DAY_RESET", "equity": self.balance, "date": str(d)})

    def drawdown(self) -> float:
        if self.peak <= 0:
            return 0.0
        return max(0.0, (self.peak - self.balance) / self.peak * 100.0)

    def record_close(self, item: dict, pnl: float, ts: pd.Timestamp) -> None:
        self.reset_day(ts)
        self.balance += pnl
        self.peak = max(self.peak, self.balance)
        self.day.day_high_equity = max(self.day.day_high_equity, self.balance)
        self.day.daily_realized_pnl += pnl
        self.day.peak_daily_realized_pnl = max(self.day.peak_daily_realized_pnl, self.day.daily_realized_pnl)
        symbol = str(item.get("symbol", ""))
        self.day.symbol_daily_pnl[symbol] += pnl
        if pnl < 0:
            self.day.global_consecutive_losses += 1
            self.day.global_daily_losses += 1
            self.day.symbol_daily_losses[symbol] += 1
            self.day.symbol_consecutive_losses[symbol] += 1
            self.day.symbol_loss_times[symbol].append(ts)
            cutoff = ts - pd.Timedelta(hours=self.cfg.symbol_rolling_loss_hours)
            while self.day.symbol_loss_times[symbol] and self.day.symbol_loss_times[symbol][0] < cutoff:
                self.day.symbol_loss_times[symbol].popleft()
            if self.day.global_consecutive_losses >= self.cfg.global_pause_after_consecutive_losses:
                until = ts + pd.Timedelta(hours=self.cfg.global_pause_hours)
                self.day.global_pause_until = max(self.day.global_pause_until or until, until)
            if self.day.global_daily_losses >= self.cfg.global_stop_after_daily_losses:
                self.day.global_stop_day = True
            if self.day.symbol_consecutive_losses[symbol] >= self.cfg.symbol_pause_after_consecutive_losses:
                self.day.symbol_block_rest_day[symbol] = True
            if len(self.day.symbol_loss_times[symbol]) >= self.cfg.symbol_rolling_loss_count:
                self.day.symbol_cooldown_until[symbol] = ts + pd.Timedelta(hours=self.cfg.symbol_rolling_loss_hours)
            if self.day.symbol_daily_losses[symbol] >= self.cfg.symbol_stop_after_daily_losses:
                self.day.symbol_block_rest_day[symbol] = True
        else:
            self.day.global_consecutive_losses = 0
            self.day.symbol_consecutive_losses[symbol] = 0

        cap = -self.cfg.symbol_daily_loss_cap_percent / 100.0 * self.day.day_start_equity
        if self.day.symbol_daily_pnl[symbol] <= cap:
            self.day.symbol_block_rest_day[symbol] = True
        if not self.day.profit_lock_enabled:
            trigger = self.cfg.profit_lock_trigger_percent / 100.0 * self.day.day_start_equity
            if self.day.daily_realized_pnl >= trigger:
                self.day.profit_lock_enabled = True
        dd = self.drawdown()
        self.max_dd = max(self.max_dd, dd)
        closed = dict(item)
        closed.update({"pnl": pnl, "equity_after": self.balance, "drawdown_after": dd, "daily_realized_pnl_after": self.day.daily_realized_pnl})
        self.trades.append(closed)
        self.events.append({"time": ts.isoformat(), "event": "EXIT", "engine_group": item.get("engine_group"), "symbol": symbol, "pnl": pnl, "equity": self.balance, "drawdown_percent": dd})

    def close_due_ict(self, now: pd.Timestamp) -> None:
        due = sorted([item for item in self.open_ict if item["exit_time"] <= now], key=lambda item: item["exit_time"])
        for item in due:
            pnl = float(item["risk_dollars"] * item["r"])
            self.record_close(item, pnl, item["exit_time"])
            self.open_ict.remove(item)

    def prune_hour_windows(self, now: pd.Timestamp) -> None:
        cutoff = now - pd.Timedelta(hours=1)
        while self.day.total_entries_hour and self.day.total_entries_hour[0] < cutoff:
            self.day.total_entries_hour.popleft()
        for queue in self.day.symbol_entries_hour.values():
            while queue and queue[0] < cutoff:
                queue.popleft()

    def ict_block_reason(self, row: dict) -> str | None:
        now = pd.Timestamp(row["entry_time"])
        symbol = str(row["symbol"])
        self.prune_hour_windows(now)
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
        if symbol in self.day.symbol_cooldown_until and now < self.day.symbol_cooldown_until[symbol]:
            return "SYMBOL_ROLLING_LOSS_COOLDOWN"
        if len(self.day.symbol_entries_hour[symbol]) >= self.cfg.max_new_trades_per_symbol_hour:
            return "TRADE_CLUSTER_SYMBOL_HOUR"
        if len(self.day.total_entries_hour) >= self.cfg.max_new_trades_total_hour:
            return "TRADE_CLUSTER_TOTAL_HOUR"
        if len(self.open_ict) >= self.cfg.max_simultaneous_ict_trades:
            return "MAX_SIMULTANEOUS_ICT_TRADES"
        return None

    def ict_risk_percent(self, row: dict) -> float:
        symbol = str(row["symbol"])
        risk = self.cfg.ict_default_risk_percent
        if self.day.global_consecutive_losses >= 1 or self.day.symbol_consecutive_losses[symbol] >= 1:
            risk = min(risk, self.cfg.ict_post_loss_risk_percent)
        if symbol == "GBPJPY":
            if self.day.symbol_daily_pnl[symbol] <= 0:
                risk = min(risk, self.cfg.gbpjpy_negative_day_risk_percent)
            else:
                risk = min(risk, self.cfg.gbpjpy_positive_day_risk_percent)
        return min(risk, self.cfg.ict_max_risk_percent)

    def run(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
        events = []
        for row in self.v12_entries:
            events.append((pd.Timestamp(row["exit_time"]), "V12_EXIT", row))
        for row in self.ict_entries:
            events.append((pd.Timestamp(row["entry_time"]), "ICT_ENTRY", row))
        events.sort(key=lambda item: (item[0], 0 if item[1] == "V12_EXIT" else 1))

        for now, kind, row in events:
            self.close_due_ict(now)
            self.reset_day(now)
            if kind == "V12_EXIT":
                item = dict(row)
                item["engine_group"] = "V12"
                self.record_close(item, float(row["fixed_pnl"]), now)
                continue
            reason = self.ict_block_reason(row)
            if reason:
                skipped = dict(row)
                skipped.update({"skip_reason": reason, "pre_equity": self.balance, "daily_pnl": self.day.daily_realized_pnl, "symbol_daily_pnl": self.day.symbol_daily_pnl[str(row["symbol"])]})
                self.skipped_ict.append(skipped)
                continue
            risk_pct = self.ict_risk_percent(row)
            item = dict(row)
            item.update({"engine_group": "ICT", "risk_percent": risk_pct, "risk_dollars": self.balance * risk_pct / 100.0, "equity_before": self.balance})
            self.open_ict.append(item)
            self.day.total_entries_hour.append(now)
            self.day.symbol_entries_hour[str(row["symbol"])].append(now)
            self.events.append({"time": now.isoformat(), "event": "ENTRY", "engine_group": "ICT", "symbol": row["symbol"], "risk_percent": risk_pct, "equity": self.balance})

        self.close_due_ict(pd.Timestamp.max)
        trades = pd.DataFrame(self.trades).sort_values("exit_time").reset_index(drop=True) if self.trades else pd.DataFrame()
        skipped = pd.DataFrame(self.skipped_ict)
        event_frame = pd.DataFrame(self.events)
        gross_profit = float(trades.loc[trades["pnl"] > 0, "pnl"].sum()) if not trades.empty else 0.0
        gross_loss = float(-trades.loc[trades["pnl"] < 0, "pnl"].sum()) if not trades.empty else 0.0
        by_engine = {}
        by_symbol = {}
        if not trades.empty:
            for engine, group in trades.groupby("engine_group"):
                pnl = group["pnl"].astype(float)
                by_engine[engine] = {"trades": int(len(group)), "net": float(pnl.sum()), "gross_profit": float(pnl[pnl > 0].sum()), "gross_loss": float(-pnl[pnl < 0].sum()), "profit_factor": float(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum()) if (pnl < 0).any() else None}
            for symbol, group in trades.groupby("symbol"):
                pnl = group["pnl"].astype(float)
                by_symbol[symbol] = {"trades": int(len(group)), "net": float(pnl.sum()), "wins": int((pnl > 0).sum()), "losses": int((pnl < 0).sum()), "profit_factor": float(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum()) if (pnl < 0).any() else None}
        summary = {
            "config": asdict(self.cfg),
            "starting_balance": self.cfg.starting_balance,
            "ending_balance": self.balance,
            "net_profit": self.balance - self.cfg.starting_balance,
            "return_percent": (self.balance / self.cfg.starting_balance - 1.0) * 100.0,
            "closed_trades": int(len(trades)),
            "skipped_ict_trades": int(len(skipped)),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "max_closed_drawdown_percent": self.max_dd,
            "by_engine_group": by_engine,
            "by_symbol": by_symbol,
            "skip_reasons": skipped["skip_reason"].value_counts().to_dict() if not skipped.empty else {},
        }
        return trades, skipped, event_frame, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V14.6 profit-preserving V12 + ICT combined replay")
    parser.add_argument("--v12-ledger", type=Path, default=DEFAULT_V12)
    parser.add_argument("--ict-trades", type=Path, default=DEFAULT_ICT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    replay = CombinedReplay(load_v12(args.v12_ledger), load_ict(args.ict_trades), V146Config())
    trades, skipped, events, summary = replay.run()
    trades.to_csv(args.out / "v14_6_profit_preserving_combined_trades.csv", index=False)
    skipped.to_csv(args.out / "v14_6_profit_preserving_combined_skipped_ict.csv", index=False)
    events.to_csv(args.out / "v14_6_profit_preserving_combined_events.csv", index=False)
    (args.out / "v14_6_profit_preserving_combined_summary.json").write_text(json.dumps(summary, indent=2, default=safe_json), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=safe_json))


if __name__ == "__main__":
    main()
