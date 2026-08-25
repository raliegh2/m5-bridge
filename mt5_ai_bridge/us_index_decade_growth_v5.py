"""US index decade-growth V5: cross-sectional trend/momentum rotation.

The model is designed for micro-futures-style forward testing (MES/MNQ/M2K)
while using broad US ETF histories as long-span research proxies. It keeps at
most one index position open, caps initial stop loss at 1.00% of equity, tapers
risk after a 5% drawdown, and hard-stops new risk at 20% drawdown.

Candidate selection is performed only on folds ending no later than 2015. A
2016+ evaluation must therefore be run separately and only once for a clean
out-of-sample decade-scale check.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
import pandas as pd

SECONDS_PER_YEAR = 365.2425 * 86_400.0


@dataclass(frozen=True)
class RotationCandidate:
    name: str
    fast_momentum_days: int
    slow_momentum_days: int
    fast_weight: float
    trend_days: int
    rebalance_days: int
    stop_atr: float
    gross_leverage_cap: float
    vol_adjust_rank: bool = True


CANDIDATES = (
    RotationCandidate("weekly_growth", 42, 126, 0.70, 150, 5, 0.85, 1.50, True),
    RotationCandidate("weekly_guarded", 63, 126, 0.65, 200, 5, 1.00, 1.35, True),
    RotationCandidate("biweekly_growth", 42, 126, 0.70, 150, 10, 0.85, 1.50, True),
    RotationCandidate("biweekly_balanced", 63, 126, 0.60, 200, 10, 1.00, 1.35, True),
    RotationCandidate("monthly_momentum", 63, 126, 0.55, 200, 21, 1.10, 1.25, False),
    RotationCandidate("fast_trend", 21, 63, 0.75, 100, 5, 0.80, 1.50, True),
)

RISK_PERCENT = 1.00
SOFT_DRAWDOWN = 0.05
HARD_DRAWDOWN = 0.20
DRAWDOWN_FLOOR = 0.25
ROUND_TRIP_BPS = 5.0


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def prepare_frame(bars: pd.DataFrame, candidate: RotationCandidate) -> pd.DataFrame:
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars missing columns: {sorted(missing)}")
    df = bars.sort_values("time").drop_duplicates("time").copy()
    close = df["close"].astype(float)
    df["atr"] = _atr(df, 14)
    df["mom_fast"] = close.pct_change(candidate.fast_momentum_days)
    df["mom_slow"] = close.pct_change(candidate.slow_momentum_days)
    df["trend"] = close.rolling(candidate.trend_days).mean()
    df["atr_pct"] = df["atr"] / close.replace(0.0, np.nan)
    raw = candidate.fast_weight * df["mom_fast"] + (1.0 - candidate.fast_weight) * df["mom_slow"]
    if candidate.vol_adjust_rank:
        df["rank_score"] = raw / df["atr_pct"].clip(lower=0.002)
    else:
        df["rank_score"] = raw
    df["eligible"] = (
        (close > df["trend"])
        & (df["mom_fast"] > 0.0)
        & (df["mom_slow"] > 0.0)
        & np.isfinite(df["rank_score"])
        & np.isfinite(df["atr"])
    )
    return df


def _drawdown_multiplier(equity: float, peak: float) -> float:
    if peak <= 0:
        return 1.0
    dd = max(0.0, (peak - equity) / peak)
    if dd >= HARD_DRAWDOWN:
        return 0.0
    if dd <= SOFT_DRAWDOWN:
        return 1.0
    span = HARD_DRAWDOWN - SOFT_DRAWDOWN
    frac = (dd - SOFT_DRAWDOWN) / span
    return max(DRAWDOWN_FLOOR, 1.0 - frac * (1.0 - DRAWDOWN_FLOOR))


def _common_times(frames: dict[str, pd.DataFrame]) -> list[int]:
    common: Optional[set[int]] = None
    for frame in frames.values():
        values = set(int(x) for x in frame["time"].tolist())
        common = values if common is None else common & values
    return sorted(common or [])


def _pf(trades: list[dict]) -> float | str:
    profits = [float(t["profit"]) for t in trades]
    wins = sum(p for p in profits if p > 0)
    losses = -sum(p for p in profits if p < 0)
    if losses > 0:
        return round(wins / losses, 4)
    return "inf" if wins > 0 else 0.0


def backtest_portfolio(
    raw_bars: dict[str, pd.DataFrame],
    candidate: RotationCandidate,
    starting_balance: float = 5_000.0,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
) -> dict:
    frames = {s: prepare_frame(df, candidate) for s, df in raw_bars.items()}
    indexed = {s: f.set_index("time", drop=False) for s, f in frames.items()}
    times = _common_times(frames)
    if start_time is not None:
        times = [t for t in times if t >= int(start_time)]
    if end_time is not None:
        times = [t for t in times if t <= int(end_time)]
    if len(times) < 3:
        raise ValueError("not enough common bars in requested V5 window")

    balance = float(starting_balance)
    peak_equity = balance
    equity_curve = [balance]
    trades: list[dict] = []
    position: Optional[dict] = None
    pending: Optional[dict] = None
    rebalance_counter = 0

    def equity_at_close(t: int) -> float:
        if position is None:
            return balance
        row = indexed[position["symbol"]].loc[t]
        return balance + (float(row["close"]) - position["entry"]) * position["units"]

    def close_position(t: int, price: float, reason: str) -> None:
        nonlocal balance, position
        if position is None:
            return
        gross = (float(price) - position["entry"]) * position["units"]
        cost = position["entry_notional"] * (ROUND_TRIP_BPS / 10_000.0)
        net = gross - cost
        balance += net
        trades.append({
            "symbol": position["symbol"],
            "signal_time": position["signal_time"],
            "entry_time": position["entry_time"],
            "exit_time": int(t),
            "entry": round(position["entry"], 6),
            "exit": round(float(price), 6),
            "units": round(position["units"], 8),
            "initial_stop": round(position["initial_stop"], 6),
            "gross_profit": round(gross, 2),
            "cost": round(cost, 2),
            "profit": round(net, 2),
            "reason": reason,
            "balance_after": round(balance, 2),
        })
        position = None

    for idx, t in enumerate(times):
        rows = {s: indexed[s].loc[t] for s in indexed}

        # A decision made on yesterday's close is executed at today's open.
        if pending is not None:
            target = pending.get("symbol")
            if position is not None:
                current_row = rows[position["symbol"]]
                current_open = float(current_row["open"])
                if current_open <= float(position["stop"]):
                    close_position(t, current_open, "GAP_STOP")
            if position is not None and position["symbol"] != target:
                close_position(t, float(rows[position["symbol"]]["open"]), "ROTATE")
            if target is None and position is not None:
                close_position(t, float(rows[position["symbol"]]["open"]), "REGIME_FLAT")
            if target is not None and position is None:
                row = rows[target]
                entry = float(row["open"])
                atr = float(pending["atr"])
                if entry > 0 and atr > 0 and np.isfinite(entry) and np.isfinite(atr):
                    mult = _drawdown_multiplier(balance, peak_equity)
                    stop_distance = candidate.stop_atr * atr
                    risk_dollars = balance * (RISK_PERCENT / 100.0) * mult
                    risk_units = risk_dollars / stop_distance if stop_distance > 0 else 0.0
                    leverage_units = balance * candidate.gross_leverage_cap * mult / entry
                    units = max(0.0, min(risk_units, leverage_units))
                    if units > 0:
                        stop = entry - stop_distance
                        position = {
                            "symbol": target,
                            "signal_time": int(pending["signal_time"]),
                            "entry_time": int(t),
                            "entry": entry,
                            "units": units,
                            "entry_notional": entry * units,
                            "initial_stop": stop,
                            "stop": stop,
                        }
            pending = None

        # Intraday stop protection, including a gap check when there was no rotation.
        if position is not None:
            row = rows[position["symbol"]]
            open_ = float(row["open"])
            if open_ <= float(position["stop"]):
                close_position(t, open_, "GAP_STOP")
            elif float(row["low"]) <= float(position["stop"]):
                close_position(t, float(position["stop"]), "TRAIL_STOP")

        equity = equity_at_close(t)
        peak_equity = max(peak_equity, equity)
        equity_curve.append(equity)

        if position is not None:
            row = rows[position["symbol"]]
            atr = float(row["atr"])
            if np.isfinite(atr) and atr > 0:
                trail = float(row["close"]) - candidate.stop_atr * atr
                position["stop"] = max(float(position["stop"]), trail)

        # Generate the next-open target using only the current completed close.
        if rebalance_counter % candidate.rebalance_days == 0 and idx < len(times) - 1:
            ranked = []
            for symbol, row in rows.items():
                if bool(row["eligible"]):
                    ranked.append((float(row["rank_score"]), symbol, float(row["atr"])))
            ranked.sort(reverse=True)
            if ranked:
                score, symbol, atr = ranked[0]
                pending = {"symbol": symbol, "atr": atr, "score": score, "signal_time": int(t)}
            else:
                pending = {"symbol": None, "signal_time": int(t)}
        rebalance_counter += 1

        if peak_equity > 0 and (peak_equity - equity) / peak_equity >= HARD_DRAWDOWN:
            if position is not None:
                close_position(t, float(rows[position["symbol"]]["close"]), "HARD_DRAWDOWN")
            break

    final_time = times[-1]
    if position is not None:
        close_position(final_time, float(indexed[position["symbol"]].loc[final_time]["close"]), "END")
        equity_curve.append(balance)

    values = np.asarray(equity_curve, dtype=float)
    peaks = np.maximum.accumulate(values)
    max_dd = float(np.max((peaks - values) / peaks)) if values.size else 0.0
    years = max((times[-1] - times[0]) / SECONDS_PER_YEAR, 1e-9)
    cagr = ((balance / starting_balance) ** (1.0 / years) - 1.0) * 100.0 if balance > 0 else -100.0
    wins = sum(1 for t in trades if float(t["profit"]) > 0)
    return {
        "model": "US_INDEX_DECADE_GROWTH_V5",
        "candidate": candidate.name,
        "starting_balance": round(starting_balance, 2),
        "final_balance": round(balance, 2),
        "net_profit": round(balance - starting_balance, 2),
        "return_pct": round((balance / starting_balance - 1.0) * 100.0, 4),
        "cagr_pct": round(cagr, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "trades": len(trades),
        "win_rate": round(wins / len(trades), 4) if trades else 0.0,
        "profit_factor": _pf(trades),
        "round_trip_bps": ROUND_TRIP_BPS,
        "risk_percent_ceiling": RISK_PERCENT,
        "gross_leverage_cap": candidate.gross_leverage_cap,
        "holdout_years": round(years, 4),
        "start_time": int(times[0]),
        "end_time": int(times[-1]),
        "trade_log": trades,
    }


def _selection_score(summary: dict) -> float:
    pf = summary["profit_factor"]
    pfv = 3.0 if pf == "inf" else min(3.0, float(pf))
    return (
        float(summary["return_pct"])
        + 4.0 * (pfv - 1.0)
        - 0.75 * float(summary["max_drawdown_pct"])
        + 0.035 * min(int(summary["trades"]), 60)
    )


def select_candidate_pre2016(raw_bars: dict[str, pd.DataFrame]) -> tuple[RotationCandidate, dict]:
    folds = (
        ("2009-01-01", "2009-12-31"),
        ("2011-01-01", "2011-12-31"),
        ("2013-01-01", "2013-12-31"),
        ("2015-01-01", "2015-12-31"),
    )
    report: dict[str, dict] = {}
    for candidate in CANDIDATES:
        fold_results = []
        for start_s, end_s in folds:
            end_epoch = int(pd.Timestamp(end_s, tz="UTC").timestamp())
            truncated = {s: df[df["time"] <= end_epoch].copy() for s, df in raw_bars.items()}
            try:
                result = backtest_portfolio(
                    truncated,
                    candidate,
                    10_000.0,
                    int(pd.Timestamp(start_s, tz="UTC").timestamp()),
                    end_epoch,
                )
                fold_results.append({
                    "year": start_s[:4],
                    "return_pct": result["return_pct"],
                    "cagr_pct": result["cagr_pct"],
                    "max_drawdown_pct": result["max_drawdown_pct"],
                    "profit_factor": result["profit_factor"],
                    "trades": result["trades"],
                    "score": round(_selection_score(result), 4),
                })
            except ValueError as exc:
                fold_results.append({"year": start_s[:4], "error": str(exc), "score": -999.0})
        valid = [x for x in fold_results if "error" not in x]
        scores = [float(x["score"]) for x in valid]
        positive = sum(1 for x in valid if float(x["return_pct"]) > 0)
        worst = min(scores) if scores else -999.0
        median = float(np.median(scores)) if scores else -999.0
        total_trades = sum(int(x.get("trades", 0)) for x in valid)
        robust = median + 0.45 * worst + 0.75 * positive + 0.01 * min(total_trades, 100)
        report[candidate.name] = {
            "folds": fold_results,
            "positive_folds": positive,
            "pre2016_trades": total_trades,
            "median_score": round(median, 4),
            "worst_score": round(worst, 4),
            "robust_score": round(robust, 4),
        }
    selected = max(CANDIDATES, key=lambda c: float(report[c.name]["robust_score"]))
    report["selected"] = selected.name
    report["selection_rule"] = "median + 0.45*worst + 0.75*positive folds + capped activity reward; all folds pre-2016"
    return selected, report


def artifact_dict(candidate: RotationCandidate, selection_report: dict) -> dict:
    return {
        "version": "US_INDEX_DECADE_GROWTH_V5",
        "candidate": asdict(candidate),
        "selection_report": selection_report,
        "risk_percent_ceiling": RISK_PERCENT,
        "soft_drawdown": SOFT_DRAWDOWN,
        "hard_drawdown": HARD_DRAWDOWN,
        "drawdown_floor": DRAWDOWN_FLOOR,
        "round_trip_bps": ROUND_TRIP_BPS,
        "intended_futures_mapping": {"VTI": "MES", "ONEQ": "MNQ", "IWM": "M2K"},
        "execution_scope": "research/demo-forward-test-only",
    }
