"""V14.7 research-only five-symbol swing and ICT strategy families.

The module generates deterministic candidates from completed H1/H4/D1 candles.
Entries occur on the next candle open. No MT5 account, order or broker API is
imported. It is designed for chronological training/validation/audit research,
not for live execution.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

import numpy as np
import pandas as pd

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")


@dataclass(frozen=True)
class StrategySpec:
    name: str
    mode: str
    family: str
    timeframe: str
    fast_ema: int
    medium_ema: int
    slow_ema: int
    lookback: int
    pullback_atr: float
    body_atr_min: float
    trend_atr_min: float
    stop_atr: float
    target_r: float
    max_holding_bars: int
    session_start: int = 0
    session_end: int = 24
    range_start: int = 0
    range_end: int = 6
    require_h4_bias: bool = True
    require_d1_bias: bool = True
    partial_fraction: float = 0.0
    partial_target_r: float = 1.0
    move_to_break_even: bool = False
    cooldown_bars: int = 1
    max_trades_per_day: int = 4
    cost_r: float = 0.12


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _completed_bias(frame: pd.DataFrame, timeframe_hours: int, prefix: str) -> pd.DataFrame:
    work = frame.copy().sort_values("time").reset_index(drop=True)
    work[f"{prefix}_ema20"] = ema(work["close"], 20)
    work[f"{prefix}_ema50"] = ema(work["close"], 50)
    work[f"{prefix}_close"] = work["close"]
    work["available"] = work["time"] + pd.Timedelta(hours=timeframe_hours)
    return work[["available", f"{prefix}_close", f"{prefix}_ema20", f"{prefix}_ema50"]]


def prepare_timeframe(
    frame: pd.DataFrame,
    h4: pd.DataFrame,
    d1: pd.DataFrame,
    spec: StrategySpec,
) -> pd.DataFrame:
    work = frame.copy().sort_values("time").reset_index(drop=True)
    hours = 4 if spec.timeframe == "H4" else 1
    work["end"] = work["time"] + pd.Timedelta(hours=hours)
    work["atr14"] = atr(work)
    work["ema_fast"] = ema(work["close"], spec.fast_ema)
    work["ema_medium"] = ema(work["close"], spec.medium_ema)
    work["ema_slow"] = ema(work["close"], spec.slow_ema)
    work["previous_close"] = work["close"].shift(1)
    work["previous_high"] = work["high"].shift(1)
    work["previous_low"] = work["low"].shift(1)
    work["previous_fast"] = work["ema_fast"].shift(1)
    work["prior_high"] = work["high"].rolling(spec.lookback, min_periods=spec.lookback).max().shift(1)
    work["prior_low"] = work["low"].rolling(spec.lookback, min_periods=spec.lookback).min().shift(1)
    work["body_atr"] = (work["close"] - work["open"]).abs() / work["atr14"].replace(0, np.nan)
    work["trend_atr"] = (work["ema_medium"] - work["ema_slow"]).abs() / work["atr14"].replace(0, np.nan)
    work["medium_slope"] = work["ema_medium"] - work["ema_medium"].shift(3)
    work["atr_ratio"] = work["atr14"] / work["atr14"].rolling(30, min_periods=30).mean()

    if spec.timeframe == "H1":
        h4_bias = _completed_bias(h4, 4, "h4")
        work = pd.merge_asof(
            work.sort_values("end"), h4_bias.sort_values("available"),
            left_on="end", right_on="available", direction="backward",
        ).drop(columns=["available"])
    else:
        work["h4_close"] = work["close"]
        work["h4_ema20"] = ema(work["close"], 20)
        work["h4_ema50"] = ema(work["close"], 50)

    d1_bias = _completed_bias(d1, 24, "d1")
    work = pd.merge_asof(
        work.sort_values("end"), d1_bias.sort_values("available"),
        left_on="end", right_on="available", direction="backward",
    ).drop(columns=["available"])

    work["day"] = work["time"].dt.floor("D")
    daily = work.groupby("day", as_index=False).agg(
        day_high=("high", "max"), day_low=("low", "min")
    )
    daily["previous_day_high"] = daily["day_high"].shift(1)
    daily["previous_day_low"] = daily["day_low"].shift(1)
    work = work.merge(
        daily[["day", "previous_day_high", "previous_day_low"]], on="day", how="left"
    )
    return work.sort_values("time").reset_index(drop=True)


def _session_range(frame: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    work = frame.copy()
    hours = work["time"].dt.hour
    range_rows = work[(hours >= spec.range_start) & (hours < spec.range_end)]
    ranges = range_rows.groupby("day", as_index=False).agg(
        session_high=("high", "max"),
        session_low=("low", "min"),
        session_bars=("time", "count"),
    )
    return work.merge(ranges, on="day", how="left")


def _biases(frame: pd.DataFrame, spec: StrategySpec) -> tuple[pd.Series, pd.Series]:
    long_bias = (
        (frame["ema_fast"] > frame["ema_medium"])
        & (frame["ema_medium"] > frame["ema_slow"])
        & (frame["medium_slope"] > 0)
    )
    short_bias = (
        (frame["ema_fast"] < frame["ema_medium"])
        & (frame["ema_medium"] < frame["ema_slow"])
        & (frame["medium_slope"] < 0)
    )
    if spec.require_h4_bias:
        long_bias &= (frame["h4_close"] > frame["h4_ema20"]) & (frame["h4_ema20"] > frame["h4_ema50"])
        short_bias &= (frame["h4_close"] < frame["h4_ema20"]) & (frame["h4_ema20"] < frame["h4_ema50"])
    if spec.require_d1_bias:
        long_bias &= (frame["d1_close"] > frame["d1_ema20"]) & (frame["d1_ema20"] > frame["d1_ema50"])
        short_bias &= (frame["d1_close"] < frame["d1_ema20"]) & (frame["d1_ema20"] < frame["d1_ema50"])
    return long_bias, short_bias


def signal_masks(frame: pd.DataFrame, spec: StrategySpec) -> tuple[pd.Series, pd.Series]:
    work = _session_range(frame, spec) if spec.family == "SESSION_BREAKOUT" else frame
    long_bias, short_bias = _biases(work, spec)
    hours = work["time"].dt.hour
    weekdays = work["time"].dt.weekday
    common = (
        (hours >= spec.session_start)
        & (hours < spec.session_end)
        & weekdays.isin((0, 1, 2, 3, 4))
        & work["atr14"].notna()
        & (work["trend_atr"] >= spec.trend_atr_min)
        & (work["body_atr"] >= spec.body_atr_min)
    )

    if spec.family == "TREND_PULLBACK":
        tolerance = spec.pullback_atr * work["atr14"]
        long_trigger = (
            (work["low"] <= work["ema_medium"] + tolerance)
            & (work["close"] > work["ema_fast"])
            & (work["close"] > work["open"])
            & (work["close"] > work["previous_high"])
        )
        short_trigger = (
            (work["high"] >= work["ema_medium"] - tolerance)
            & (work["close"] < work["ema_fast"])
            & (work["close"] < work["open"])
            & (work["close"] < work["previous_low"])
        )
    elif spec.family == "BREAKOUT":
        long_trigger = (work["close"] > work["prior_high"]) & (work["close"] > work["open"])
        short_trigger = (work["close"] < work["prior_low"]) & (work["close"] < work["open"])
    elif spec.family == "VOLATILITY_BREAKOUT":
        long_trigger = (
            (work["atr_ratio"] >= 1.05)
            & (work["close"] > work["prior_high"])
            & (work["close"] > work["open"])
        )
        short_trigger = (
            (work["atr_ratio"] >= 1.05)
            & (work["close"] < work["prior_low"])
            & (work["close"] < work["open"])
        )
    elif spec.family == "EMA_RECLAIM":
        tolerance = spec.pullback_atr * work["atr14"]
        long_trigger = (
            (work["previous_close"] <= work["previous_fast"] + tolerance)
            & (work["close"] > work["ema_fast"])
            & (work["close"] > work["open"])
        )
        short_trigger = (
            (work["previous_close"] >= work["previous_fast"] - tolerance)
            & (work["close"] < work["ema_fast"])
            & (work["close"] < work["open"])
        )
    elif spec.family == "PREVIOUS_DAY_SWEEP":
        long_trigger = (
            (work["low"] < work["previous_day_low"])
            & (work["close"] > work["previous_day_low"])
            & (work["close"] > work["open"])
        )
        short_trigger = (
            (work["high"] > work["previous_day_high"])
            & (work["close"] < work["previous_day_high"])
            & (work["close"] < work["open"])
        )
    elif spec.family == "SESSION_BREAKOUT":
        minimum_bars = max(3, spec.range_end - spec.range_start - 1)
        common &= work["session_bars"] >= minimum_bars
        long_trigger = (work["close"] > work["session_high"]) & (work["close"] > work["open"])
        short_trigger = (work["close"] < work["session_low"]) & (work["close"] < work["open"])
    elif spec.family == "FVG_CONTINUATION":
        bullish_gap = work["low"] > work["high"].shift(2)
        bearish_gap = work["high"] < work["low"].shift(2)
        long_trigger = bullish_gap & (work["close"] > work["open"])
        short_trigger = bearish_gap & (work["close"] < work["open"])
    else:
        raise ValueError(f"Unsupported family: {spec.family}")
    return common & long_bias & long_trigger, common & short_bias & short_trigger


def simulate_exit(
    frame: pd.DataFrame,
    signal_index: int,
    side: int,
    stop_price: float,
    spec: StrategySpec,
) -> tuple[pd.Timestamp, float]:
    if signal_index + 1 >= len(frame):
        return pd.Timestamp(frame.iloc[signal_index]["end"]), 0.0
    entry_row = frame.iloc[signal_index + 1]
    entry = float(entry_row["open"])
    initial_risk = (entry - stop_price) * side
    if not np.isfinite(initial_risk) or initial_risk <= 0:
        return pd.Timestamp(entry_row["end"]), 0.0

    final_price = entry + side * spec.target_r * initial_risk
    partial_price = entry + side * spec.partial_target_r * initial_risk
    current_stop = float(stop_price)
    partial_taken = False
    realized = 0.0
    remaining = 1.0
    last_index = min(len(frame) - 1, signal_index + spec.max_holding_bars)

    for index in range(signal_index + 1, last_index + 1):
        row = frame.iloc[index]
        low, high = float(row["low"]), float(row["high"])
        stop_hit = low <= current_stop if side > 0 else high >= current_stop
        if stop_hit:
            residual = (current_stop - entry) * side / initial_risk
            return pd.Timestamp(row["end"]), float(realized + remaining * residual)

        if spec.partial_fraction > 0 and not partial_taken:
            partial_hit = high >= partial_price if side > 0 else low <= partial_price
            if partial_hit:
                realized += spec.partial_fraction * spec.partial_target_r
                remaining = 1.0 - spec.partial_fraction
                partial_taken = True
                if spec.move_to_break_even:
                    current_stop = entry

        final_hit = high >= final_price if side > 0 else low <= final_price
        if final_hit:
            return pd.Timestamp(row["end"]), float(realized + remaining * spec.target_r)

    final = frame.iloc[last_index]
    residual = (float(final["close"]) - entry) * side / initial_risk
    return pd.Timestamp(final["end"]), float(realized + remaining * residual)


def generate_candidates(
    symbol: str,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    d1: pd.DataFrame,
    spec: StrategySpec,
) -> pd.DataFrame:
    symbol = symbol.upper()
    source = h4 if spec.timeframe == "H4" else h1
    frame = prepare_timeframe(source, h4, d1, spec)
    long_signal, short_signal = signal_masks(frame, spec)
    rows: list[dict] = []
    last_index = -10_000
    daily_counts: dict[pd.Timestamp, int] = {}

    for raw_index in np.flatnonzero((long_signal | short_signal).to_numpy()):
        index = int(raw_index)
        if index - last_index < spec.cooldown_bars:
            continue
        day = pd.Timestamp(frame.iloc[index]["day"])
        if daily_counts.get(day, 0) >= spec.max_trades_per_day:
            continue
        row = frame.iloc[index]
        side = 1 if bool(long_signal.iloc[index]) else -1
        atr_value = float(row["atr14"])
        if not np.isfinite(atr_value) or atr_value <= 0:
            continue
        next_open = float(frame.iloc[index + 1]["open"]) if index + 1 < len(frame) else float(row["close"])
        if side > 0:
            stop = min(float(row["low"]) - 0.05 * atr_value, next_open - spec.stop_atr * atr_value)
        else:
            stop = max(float(row["high"]) + 0.05 * atr_value, next_open + spec.stop_atr * atr_value)
        exit_time, result_r = simulate_exit(frame, index, side, stop, spec)
        rows.append(
            {
                "symbol": symbol,
                "mode": spec.mode,
                "engine": f"{symbol}_{spec.mode}_{spec.name}".upper(),
                "family": spec.family,
                "profile": spec.name,
                "timeframe": spec.timeframe,
                "side": "BUY" if side > 0 else "SELL",
                "entry_time": pd.Timestamp(row["end"]),
                "exit_time": exit_time,
                "r_multiple": float(result_r),
                "selection_cost_r": float(spec.cost_r),
                "stop_atr": float(spec.stop_atr),
                "target_r": float(spec.target_r),
                "partial_fraction": float(spec.partial_fraction),
                "partial_target_r": float(spec.partial_target_r),
                "max_trades_per_day": int(spec.max_trades_per_day),
            }
        )
        last_index = index
        daily_counts[day] = daily_counts.get(day, 0) + 1
    return pd.DataFrame(rows)


def strategy_specs(symbol: str) -> tuple[StrategySpec, ...]:
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"Unsupported symbol: {symbol}")
    volatility = {"GBPJPY": 1.15, "USDJPY": 1.05}.get(symbol, 1.0)
    asia_start = 0 if symbol in {"AUDUSD", "USDJPY"} else 6
    asia_end = 11 if symbol in {"AUDUSD", "USDJPY"} else 13

    swing = [
        StrategySpec("SWING_PULLBACK_20", "SWING", "TREND_PULLBACK", "H4", 10, 20, 50, 10, 0.25, 0.12, 0.08, 1.25 * volatility, 2.2, 36, cost_r=0.04, max_trades_per_day=2),
        StrategySpec("SWING_PULLBACK_RUNNER", "SWING", "TREND_PULLBACK", "H4", 8, 21, 55, 12, 0.35, 0.10, 0.06, 1.35 * volatility, 2.8, 48, partial_fraction=0.35, partial_target_r=1.0, move_to_break_even=True, cost_r=0.04, max_trades_per_day=2),
        StrategySpec("SWING_BREAKOUT_12", "SWING", "BREAKOUT", "H4", 10, 20, 50, 12, 0.0, 0.18, 0.08, 1.30 * volatility, 2.5, 40, cost_r=0.04, max_trades_per_day=2),
        StrategySpec("SWING_BREAKOUT_24", "SWING", "BREAKOUT", "H4", 10, 30, 80, 24, 0.0, 0.16, 0.06, 1.45 * volatility, 3.0, 60, partial_fraction=0.30, partial_target_r=1.2, move_to_break_even=True, cost_r=0.04, max_trades_per_day=2),
        StrategySpec("SWING_VOL_EXPANSION", "SWING", "VOLATILITY_BREAKOUT", "H4", 8, 21, 55, 16, 0.0, 0.22, 0.08, 1.40 * volatility, 2.6, 44, cost_r=0.04, max_trades_per_day=2),
        StrategySpec("SWING_EMA_RECLAIM", "SWING", "EMA_RECLAIM", "H4", 10, 30, 80, 10, 0.20, 0.10, 0.05, 1.20 * volatility, 2.0, 32, partial_fraction=0.35, partial_target_r=0.9, move_to_break_even=True, cost_r=0.04, max_trades_per_day=2),
    ]

    ict = [
        StrategySpec("ICT_LONDON_PULLBACK", "ICT", "TREND_PULLBACK", "H1", 8, 21, 55, 5, 0.30, 0.12, 0.07, 0.90 * volatility, 1.7, 8, 6, 13, partial_fraction=0.45, partial_target_r=0.9, move_to_break_even=True, max_trades_per_day=5),
        StrategySpec("ICT_NY_PULLBACK", "ICT", "TREND_PULLBACK", "H1", 8, 21, 55, 5, 0.35, 0.14, 0.07, 0.95 * volatility, 1.8, 8, 12, 19, partial_fraction=0.45, partial_target_r=1.0, move_to_break_even=True, max_trades_per_day=4),
        StrategySpec("ICT_FAST_RECLAIM", "ICT", "EMA_RECLAIM", "H1", 5, 13, 34, 4, 0.35, 0.10, 0.05, 0.80 * volatility, 1.4, 6, asia_start, 19, require_h4_bias=False, partial_fraction=0.40, partial_target_r=0.8, move_to_break_even=True, max_trades_per_day=6),
        StrategySpec("ICT_MOMENTUM_BREAKOUT_4", "ICT", "BREAKOUT", "H1", 8, 21, 55, 4, 0.0, 0.18, 0.08, 0.95 * volatility, 1.8, 8, 6, 19, partial_fraction=0.40, partial_target_r=1.0, move_to_break_even=True, max_trades_per_day=5),
        StrategySpec("ICT_MOMENTUM_BREAKOUT_8", "ICT", "VOLATILITY_BREAKOUT", "H1", 8, 21, 55, 8, 0.0, 0.20, 0.08, 1.00 * volatility, 2.0, 10, 6, 19, partial_fraction=0.35, partial_target_r=1.0, move_to_break_even=True, max_trades_per_day=4),
        StrategySpec("ICT_PREVIOUS_DAY_SWEEP", "ICT", "PREVIOUS_DAY_SWEEP", "H1", 8, 21, 55, 5, 0.0, 0.12, 0.05, 0.85 * volatility, 1.6, 8, 6, 19, require_d1_bias=False, partial_fraction=0.50, partial_target_r=0.9, move_to_break_even=True, max_trades_per_day=4),
        StrategySpec("ICT_SESSION_BREAKOUT", "ICT", "SESSION_BREAKOUT", "H1", 8, 21, 55, 5, 0.0, 0.15, 0.06, 0.95 * volatility, 1.8, 8, asia_end - 1, 18, 0, asia_end - 1, partial_fraction=0.40, partial_target_r=1.0, move_to_break_even=True, max_trades_per_day=4),
        StrategySpec("ICT_FVG_CONTINUATION", "ICT", "FVG_CONTINUATION", "H1", 8, 21, 55, 5, 0.0, 0.12, 0.06, 0.90 * volatility, 1.7, 8, 6, 19, partial_fraction=0.40, partial_target_r=0.9, move_to_break_even=True, max_trades_per_day=5),
    ]

    # Add relaxed-bias variants only as separate research candidates. They remain
    # subject to the same chronological validation and untouched audit gates.
    relaxed = [
        replace(ict[0], name="ICT_LONDON_PULLBACK_H4", require_d1_bias=False),
        replace(ict[3], name="ICT_BREAKOUT_H4", require_d1_bias=False),
        replace(ict[5], name="ICT_SWEEP_H4", require_d1_bias=False, require_h4_bias=True),
    ]
    return tuple(swing + ict + relaxed)


def generate_symbol_candidates(
    symbol: str, h1: pd.DataFrame, h4: pd.DataFrame, d1: pd.DataFrame
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for spec in strategy_specs(symbol):
        generated = generate_candidates(symbol, h1, h4, d1, spec)
        if not generated.empty:
            frames.append(generated)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    return output.sort_values(["entry_time", "symbol", "mode", "engine"]).reset_index(drop=True)


def validate_specs(specs: Iterable[StrategySpec]) -> None:
    for spec in specs:
        if spec.mode not in {"SWING", "ICT"}:
            raise RuntimeError(f"Invalid mode: {spec.mode}")
        if spec.timeframe not in {"H1", "H4"}:
            raise RuntimeError(f"Invalid timeframe: {spec.timeframe}")
        if not 0 <= spec.partial_fraction < 1:
            raise RuntimeError(f"Invalid partial fraction: {spec.name}")
        if spec.stop_atr <= 0 or spec.target_r <= 0:
            raise RuntimeError(f"Invalid risk geometry: {spec.name}")
        if not 0 <= spec.session_start < spec.session_end <= 24:
            raise RuntimeError(f"Invalid session: {spec.name}")


for _symbol in SYMBOLS:
    validate_specs(strategy_specs(_symbol))
