"""Compact frozen GBPUSD V4 swing backtester for portfolio window studies."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PIP = 0.0001
PIP_VALUE = 10.0


@dataclass(frozen=True)
class V4Config:
    initial_balance: float = 5000.0
    risk_percent: float = 0.35
    spread_floor_pips: float = 0.8
    slippage_pips: float = 0.3
    swap_pips_per_day: float = -0.2
    stop_atr: float = 1.5
    target_r: float = 3.0
    partial_r: float = 1.0
    partial_fraction: float = 0.5
    trail_atr: float = 2.5
    max_hold_bars: int = 72
    min_stop_pips: float = 20.0
    max_stop_pips: float = 150.0
    min_lot: float = 0.01
    max_lot: float = 2.0
    lot_step: float = 0.01
    daily_loss_limit: float = 250.0
    total_loss_limit: float = 500.0
    soft_drawdown_percent: float = 6.0


@dataclass
class Position:
    side: int
    variant: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    initial_risk_price: float
    initial_risk_dollars: float
    lots: float
    bars: int = 0
    highest: float = 0.0
    lowest: float = 0.0
    remaining_fraction: float = 1.0
    partial_done: bool = False
    booked_pnl: float = 0.0


def load_mt5_h4(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep="\t")
    if len(frame.columns) == 1:
        frame = pd.read_csv(path)
    frame = frame.rename(columns={
        "<OPEN>": "open", "<HIGH>": "high", "<LOW>": "low",
        "<CLOSE>": "close", "<TICKVOL>": "tick_volume",
        "<SPREAD>": "spread_points", "spread": "spread_points",
    })
    if "<DATE>" in frame and "<TIME>" in frame:
        frame["time"] = pd.to_datetime(
            frame["<DATE>"].astype(str) + " " + frame["<TIME>"].astype(str),
            format="%Y.%m.%d %H:%M:%S", utc=True,
        )
    else:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
    if "spread_points" not in frame:
        frame["spread_points"] = 0.0
    return frame[["time", "open", "high", "low", "close", "tick_volume", "spread_points"]].sort_values("time").drop_duplicates("time").reset_index(drop=True)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame.close.shift(1)
    true_range = pd.concat([
        frame.high - frame.low,
        (frame.high - previous).abs(),
        (frame.low - previous).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up = frame.high.diff()
    down = -frame.low.diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    average_range = atr(frame, period)
    plus_di = 100 * plus.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / average_range
    minus_di = 100 * minus.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / average_range
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def prepare(h4: pd.DataFrame) -> pd.DataFrame:
    frame = h4.copy()
    frame["bar_end"] = frame.time + pd.Timedelta(hours=4)
    frame["atr14"] = atr(frame)
    frame["adx14"] = adx(frame)
    frame["avg_volume"] = frame.tick_volume.rolling(20).mean()
    frame["volume_ratio"] = frame.tick_volume / frame.avg_volume
    frame["atr_ratio"] = frame.atr14 / frame.atr14.rolling(20).mean()
    candle_range = (frame.high - frame.low).replace(0, np.nan)
    frame["body_ratio"] = (frame.close - frame.open).abs() / candle_range
    frame["close_location"] = (frame.close - frame.low) / candle_range
    frame["primary_high"] = frame.high.shift(1).rolling(20).max()
    frame["primary_low"] = frame.low.shift(1).rolling(20).min()
    frame["secondary_high"] = frame.high.shift(1).rolling(45).max()
    frame["secondary_low"] = frame.low.shift(1).rolling(45).min()

    daily = frame.set_index("bar_end").resample("1D", label="right", closed="right").agg(
        close=("close", "last"), high=("high", "max"), low=("low", "min")
    ).dropna()
    daily["daily_time"] = daily.index
    daily["ema20_d1"] = ema(daily.close, 20)
    daily["ema50_d1"] = ema(daily.close, 50)
    merged = pd.merge_asof(
        frame.sort_values("bar_end"),
        daily[["daily_time", "close", "ema20_d1", "ema50_d1"]].sort_values("daily_time"),
        left_on="bar_end", right_on="daily_time", direction="backward",
        suffixes=("", "_d1"),
    )
    merged["hour"] = merged.bar_end.dt.hour
    merged["weekday"] = merged.bar_end.dt.dayofweek
    return merged


def choose_signal(row: pd.Series) -> Optional[tuple[int, str]]:
    if pd.isna(row.ema20_d1) or pd.isna(row.ema50_d1):
        return None
    trend = 0
    if row.ema20_d1 > row.ema50_d1 and row.close_d1 > row.ema20_d1:
        trend = 1
    elif row.ema20_d1 < row.ema50_d1 and row.close_d1 < row.ema20_d1:
        trend = -1
    if not trend:
        return None
    primary = (
        row.hour == 16 and row.adx14 >= 18 and row.volume_ratio >= 0.70
        and row.body_ratio >= 0.30 and row.atr_ratio >= 1.0
    )
    if primary:
        if trend == 1 and row.close_location >= 0.70 and row.close > row.primary_high:
            return 1, "PRIMARY_16UTC_BREAKOUT"
        if trend == -1 and row.close_location <= 0.30 and row.close < row.primary_low:
            return -1, "PRIMARY_16UTC_BREAKOUT"
    secondary = (
        row.hour == 12 and row.weekday != 4 and row.adx14 >= 12
        and row.volume_ratio >= 0.70 and row.body_ratio >= 0.50
        and row.atr_ratio >= 1.0
    )
    if secondary:
        if trend == 1 and row.close_location >= 0.60 and row.close > row.secondary_high:
            return 1, "SECONDARY_12UTC_BREAKOUT"
        if trend == -1 and row.close_location <= 0.40 and row.close < row.secondary_low:
            return -1, "SECONDARY_12UTC_BREAKOUT"
    return None


def round_lot(raw: float, cfg: V4Config) -> float:
    stepped = math.floor(raw / cfg.lot_step) * cfg.lot_step
    return round(min(max(stepped, cfg.min_lot), cfg.max_lot), 2)


def backtest(h4: pd.DataFrame, cfg: V4Config = V4Config(),
             start: Optional[str] = None, end: Optional[str] = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    data = prepare(h4)
    start_ts = pd.Timestamp(start) if start else None
    end_ts = pd.Timestamp(end) if end else None
    if start_ts is not None and start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    if end_ts is not None and end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    balance = cfg.initial_balance
    initial = balance
    peak = balance
    maximum_drawdown = 0.0
    current_day = None
    day_start = balance
    disabled = False
    position: Optional[Position] = None
    trades, equity_rows = [], []

    for index in range(250, len(data) - 1):
        row = data.iloc[index]
        timestamp = row.bar_end
        if start_ts is not None and timestamp < start_ts:
            continue
        if end_ts is not None and timestamp >= end_ts:
            break
        if timestamp.date() != current_day:
            current_day = timestamp.date()
            day_start = balance
        equity = balance
        if position is not None:
            unrealized = position.side * (row.close - position.entry) / PIP * PIP_VALUE * position.lots * position.remaining_fraction
            equity = balance + unrealized
            peak = max(peak, equity)
            maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)
            old_stop = position.stop
            exit_price = reason = None
            if position.side == 1:
                if row.open <= old_stop:
                    exit_price, reason = row.open, "GAP_STOP"
                elif row.low <= old_stop:
                    exit_price, reason = old_stop, "STOP_OR_TRAIL"
                else:
                    partial_price = position.entry + cfg.partial_r * position.initial_risk_price
                    if not position.partial_done and row.high >= partial_price:
                        spread = max(row.spread_points / 10, cfg.spread_floor_pips)
                        executed = partial_price - (spread / 2 + cfg.slippage_pips) * PIP
                        pnl = (executed - position.entry) / PIP * PIP_VALUE * position.lots * cfg.partial_fraction
                        balance += pnl
                        position.booked_pnl += pnl
                        position.remaining_fraction -= cfg.partial_fraction
                        position.partial_done = True
                        position.stop = max(position.stop, position.entry)
                    if row.high >= position.target:
                        exit_price, reason = position.target, "TARGET"
                    elif position.bars >= cfg.max_hold_bars:
                        exit_price, reason = row.close, "TIME"
            else:
                if row.open >= old_stop:
                    exit_price, reason = row.open, "GAP_STOP"
                elif row.high >= old_stop:
                    exit_price, reason = old_stop, "STOP_OR_TRAIL"
                else:
                    partial_price = position.entry - cfg.partial_r * position.initial_risk_price
                    if not position.partial_done and row.low <= partial_price:
                        spread = max(row.spread_points / 10, cfg.spread_floor_pips)
                        executed = partial_price + (spread / 2 + cfg.slippage_pips) * PIP
                        pnl = (position.entry - executed) / PIP * PIP_VALUE * position.lots * cfg.partial_fraction
                        balance += pnl
                        position.booked_pnl += pnl
                        position.remaining_fraction -= cfg.partial_fraction
                        position.partial_done = True
                        position.stop = min(position.stop, position.entry)
                    if row.low <= position.target:
                        exit_price, reason = position.target, "TARGET"
                    elif position.bars >= cfg.max_hold_bars:
                        exit_price, reason = row.close, "TIME"
            if exit_price is not None:
                spread = max(row.spread_points / 10, cfg.spread_floor_pips)
                executed = exit_price - position.side * (spread / 2 + cfg.slippage_pips) * PIP
                pnl = position.side * (executed - position.entry) / PIP * PIP_VALUE * position.lots * position.remaining_fraction
                holding_days = max(1, math.ceil(position.bars / 6))
                pnl += cfg.swap_pips_per_day * holding_days * PIP_VALUE * position.lots * position.remaining_fraction
                before = balance - position.booked_pnl
                balance += pnl
                total_pnl = position.booked_pnl + pnl
                trades.append({
                    **asdict(position), "exit_time": timestamp, "exit": executed,
                    "pnl": total_pnl, "balance_before": before,
                    "balance": balance, "return_fraction": total_pnl / before,
                    "r_multiple": total_pnl / position.initial_risk_dollars,
                    "reason": reason, "engine": "V4_SWING",
                })
                position = None
                equity = balance
                peak = max(peak, equity)
            else:
                position.bars += 1
                position.highest = max(position.highest, row.high)
                position.lowest = min(position.lowest, row.low)
                favorable = position.side * (row.close - position.entry)
                if favorable >= position.initial_risk_price:
                    if position.side == 1:
                        candidate = position.highest - cfg.trail_atr * row.atr14
                        if candidate < row.close:
                            position.stop = max(position.stop, candidate)
                    else:
                        candidate = position.lowest + cfg.trail_atr * row.atr14
                        if candidate > row.close:
                            position.stop = min(position.stop, candidate)
        equity_rows.append({"time": timestamp, "balance": balance, "equity": equity})
        if position is not None or disabled:
            continue
        if max(0.0, day_start - balance) >= cfg.daily_loss_limit:
            continue
        if max(0.0, initial - balance) >= cfg.total_loss_limit:
            disabled = True
            continue
        if (peak - balance) / peak * 100 >= cfg.soft_drawdown_percent:
            continue
        selected = choose_signal(row)
        if selected is None:
            continue
        side, variant = selected
        next_bar = data.iloc[index + 1]
        if end_ts is not None and next_bar.bar_end >= end_ts:
            continue
        spread = max(next_bar.spread_points / 10, cfg.spread_floor_pips)
        entry = next_bar.open + side * (spread / 2 + cfg.slippage_pips) * PIP
        distance = min(max(cfg.stop_atr * row.atr14, cfg.min_stop_pips * PIP), cfg.max_stop_pips * PIP)
        lots = round_lot(balance * cfg.risk_percent / 100 / ((distance / PIP) * PIP_VALUE), cfg)
        risk = distance / PIP * PIP_VALUE * lots
        position = Position(
            side, variant, next_bar.bar_end, entry, entry - side * distance,
            entry + side * cfg.target_r * distance, distance, risk, lots,
            highest=entry, lowest=entry,
        )

    trades_frame = pd.DataFrame(trades)
    equity_frame = pd.DataFrame(equity_rows)
    gains = trades_frame.loc[trades_frame.pnl > 0, "pnl"].sum() if not trades_frame.empty else 0.0
    losses = -trades_frame.loc[trades_frame.pnl < 0, "pnl"].sum() if not trades_frame.empty else 0.0
    metrics = {
        "initial_balance": initial,
        "ending_balance": balance,
        "net_profit": balance - initial,
        "return_percent": (balance / initial - 1) * 100,
        "trades": len(trades_frame),
        "win_rate": float((trades_frame.pnl > 0).mean()) if not trades_frame.empty else 0.0,
        "profit_factor": float(gains / losses) if losses else float("inf"),
        "maximum_drawdown_percent": maximum_drawdown * 100,
    }
    return trades_frame, equity_frame, metrics
