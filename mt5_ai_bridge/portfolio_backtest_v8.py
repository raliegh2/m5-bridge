"""Synchronized risk simulator for completed strategy trade candidates."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from .portfolio_v8_config import ENGINE_PRIORITY, PortfolioV8Config


REQUIRED = {
    "symbol", "engine", "setup", "side", "entry_time", "exit_time",
    "risk_percent", "r_multiple",
}


def run_portfolio_backtest(candidates: pd.DataFrame, cfg: PortfolioV8Config = PortfolioV8Config()):
    missing = REQUIRED - set(candidates.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    data = candidates.copy()
    data["entry_time"] = pd.to_datetime(data["entry_time"], utc=True)
    data["exit_time"] = pd.to_datetime(data["exit_time"], utc=True)
    data = data.sort_values(["entry_time", "engine"]).reset_index(drop=True)
    data["candidate_id"] = data.index
    data["priority"] = data["engine"].map(ENGINE_PRIORITY).fillna(99).astype(int)
    rows = data.set_index("candidate_id").to_dict("index")

    events = []
    for candidate_id, row in rows.items():
        events.append((row["entry_time"], 1, row["priority"], "ENTRY", candidate_id))
        exit_order = 2 if row["exit_time"] <= row["entry_time"] else 0
        events.append((row["exit_time"], exit_order, row["priority"], "EXIT", candidate_id))
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    balance = cfg.initial_balance
    peak = balance
    stress_peak = balance
    max_realized_dd = 0.0
    max_stress_dd = 0.0
    max_positions = 0
    max_open_risk = 0.0
    open_positions = {}
    completed = []
    rejected = []
    equity = []
    daily_pnl = defaultdict(float)
    weekly_pnl = defaultdict(float)
    weekly_start = {}

    def open_risk_dollars():
        return sum(position["risk_dollars"] for position in open_positions.values())

    def reject(candidate_id, timestamp, reason):
        row = rows[candidate_id]
        rejected.append({
            "candidate_id": candidate_id,
            "time": timestamp,
            "symbol": row["symbol"],
            "engine": row["engine"],
            "setup": row["setup"],
            "reason": reason,
        })

    for timestamp, _, _, event_type, candidate_id in events:
        row = rows[candidate_id]
        day = timestamp.date()
        week = (timestamp - pd.Timedelta(days=timestamp.weekday())).date()
        weekly_start.setdefault(week, balance)

        if event_type == "EXIT":
            position = open_positions.pop(candidate_id, None)
            if position is None:
                continue
            pnl = position["risk_dollars"] * position["r_multiple"]
            balance += pnl
            daily_pnl[day] += pnl
            weekly_pnl[week] += pnl
            completed.append({**position, "pnl": pnl, "exit_balance": balance})
        else:
            dd_percent = (peak - balance) / peak * 100 if peak else 0.0
            if cfg.initial_balance - balance >= cfg.total_loss_limit:
                reject(candidate_id, timestamp, "TOTAL_LOSS_LIMIT")
                continue
            if dd_percent >= cfg.drawdown_pause_percent:
                reject(candidate_id, timestamp, "DRAWDOWN_PAUSE")
                continue
            if -daily_pnl[day] >= cfg.daily_loss_limit:
                reject(candidate_id, timestamp, "DAILY_LOSS_LIMIT")
                continue
            weekly_limit = weekly_start[week] * cfg.weekly_loss_percent / 100
            if -weekly_pnl[week] >= weekly_limit:
                reject(candidate_id, timestamp, "WEEKLY_LOSS_LIMIT")
                continue

            risk_percent = float(row["risk_percent"])
            if dd_percent >= cfg.drawdown_throttle_percent:
                risk_percent *= 0.5
            risk_dollars = balance * risk_percent / 100
            proposed = {
                **row,
                "risk_percent_actual": risk_percent,
                "risk_dollars": risk_dollars,
                "entry_balance": balance,
            }

            if len(open_positions) >= cfg.max_positions:
                reject(candidate_id, timestamp, "MAX_POSITIONS")
                continue
            if open_risk_dollars() + risk_dollars > balance * cfg.max_open_risk_percent / 100:
                reject(candidate_id, timestamp, "MAX_OPEN_RISK")
                continue

            same_symbol = [p for p in open_positions.values() if p["symbol"] == row["symbol"]]
            if same_symbol:
                allowed = False
                if cfg.allow_aligned_gbpusd_engines and row["symbol"] == "GBPUSD" and len(same_symbol) == 1:
                    other = same_symbol[0]
                    engines = {other["engine"], row["engine"]}
                    allowed = (
                        engines == {"GBPUSD_SWING_V6", "GBPUSD_SATELLITE_V2"}
                        and other["side"] == row["side"]
                    )
                if not allowed:
                    reject(candidate_id, timestamp, "SAME_SYMBOL_CONFLICT")
                    continue

            gbp = [
                (p["side"], p["risk_dollars"])
                for p in open_positions.values()
                if p["symbol"] in {"GBPUSD", "GBPJPY"}
            ]
            if row["symbol"] in {"GBPUSD", "GBPJPY"}:
                gbp.append((row["side"], risk_dollars))
            if gbp:
                directions = {int(np.sign(side)) for side, _ in gbp if side != 0}
                cap = cfg.gbp_aligned_risk_percent if len(directions) <= 1 else cfg.gbp_mixed_risk_percent
                if sum(value for _, value in gbp) > balance * cap / 100:
                    reject(candidate_id, timestamp, "GBP_CLUSTER_CAP")
                    continue

            open_positions[candidate_id] = proposed

        peak = max(peak, balance)
        max_realized_dd = max(max_realized_dd, (peak - balance) / peak if peak else 0.0)
        stress = balance - open_risk_dollars()
        stress_peak = max(stress_peak, stress)
        max_stress_dd = max(max_stress_dd, (stress_peak - stress) / stress_peak if stress_peak else 0.0)
        max_positions = max(max_positions, len(open_positions))
        risk_percent_open = open_risk_dollars() / balance * 100 if balance else 0.0
        max_open_risk = max(max_open_risk, risk_percent_open)
        equity.append({
            "time": timestamp,
            "balance": balance,
            "stress_equity": stress,
            "open_positions": len(open_positions),
            "open_risk_percent": risk_percent_open,
        })

    completed_frame = pd.DataFrame(completed)
    if not completed_frame.empty:
        completed_frame = completed_frame.sort_values("exit_time")
    rejected_frame = pd.DataFrame(rejected)
    equity_frame = pd.DataFrame(equity)
    gross_profit = completed_frame.loc[completed_frame["pnl"] > 0, "pnl"].sum() if not completed_frame.empty else 0.0
    gross_loss = -completed_frame.loc[completed_frame["pnl"] < 0, "pnl"].sum() if not completed_frame.empty else 0.0
    metrics = {
        "initial_balance": cfg.initial_balance,
        "ending_balance": balance,
        "net_profit": balance - cfg.initial_balance,
        "return_percent": (balance / cfg.initial_balance - 1) * 100,
        "trades": int(len(completed_frame)),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else None,
        "win_rate": float((completed_frame["pnl"] > 0).mean()) if not completed_frame.empty else 0.0,
        "maximum_realized_drawdown_percent": max_realized_dd * 100,
        "maximum_open_risk_stress_drawdown_percent": max_stress_dd * 100,
        "maximum_concurrent_positions": max_positions,
        "maximum_open_risk_percent": max_open_risk,
        "rejected_candidates": int(len(rejected_frame)),
        "rejection_reasons": rejected_frame["reason"].value_counts().to_dict() if not rejected_frame.empty else {},
    }
    return completed_frame, rejected_frame, equity_frame, metrics
