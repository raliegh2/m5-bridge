"""V14.10 research-only alternative FX engine families.

These engines are independent of the existing SWING and ICT candidate streams:

* SESSION_BREAKOUT: completed Asian/London range expansion;
* MOMENTUM: volatility and volume-confirmed continuation;
* MEAN_REVERSION: low-trend statistical deviation fades.

Signals use completed H1/H4/D1 candles and enter on the next candle open. Exit
simulation is deliberately conservative: when a stop and target are both inside
the same bar, the stop is evaluated first. This module imports no MT5 account,
order, or broker API and cannot transmit trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")


@dataclass(frozen=True)
class AlternativeSpec:
    name: str
    mode: str
    family: str
    timeframe: str
    session_start: int
    session_end: int
    range_start: int = 0
    range_end: int = 6
    lookback: int = 20
    threshold: float = 1.0
    stop_atr: float = 1.0
    target_r: float = 1.8
    max_holding_bars: int = 12
    partial_fraction: float = 0.0
    partial_target_r: float = 1.0
    move_to_break_even: bool = False
    cooldown_bars: int = 3
    max_trades_per_day: int = 2
    require_h4_bias: bool = True
    require_d1_bias: bool = False
    cost_r: float = 0.10


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
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def completed_bias(frame: pd.DataFrame, hours: int, prefix: str) -> pd.DataFrame:
    work = frame.copy().sort_values("time").reset_index(drop=True)
    work[f"{prefix}_close"] = work["close"]
    work[f"{prefix}_ema20"] = ema(work["close"], 20)
    work[f"{prefix}_ema50"] = ema(work["close"], 50)
    work[f"{prefix}_atr14"] = atr(work)
    work["available"] = work["time"] + pd.Timedelta(hours=hours)
    return work[
        [
            "available",
            f"{prefix}_close",
            f"{prefix}_ema20",
            f"{prefix}_ema50",
            f"{prefix}_atr14",
        ]
    ]


def prepare_frame(
    source: pd.DataFrame,
    h4: pd.DataFrame,
    d1: pd.DataFrame,
    timeframe: str,
) -> pd.DataFrame:
    work = source.copy().sort_values("time").reset_index(drop=True)
    hours = 4 if timeframe == "H4" else 1
    work["end"] = work["time"] + pd.Timedelta(hours=hours)
    work["atr14"] = atr(work)
    work["ema8"] = ema(work["close"], 8)
    work["ema20"] = ema(work["close"], 20)
    work["ema50"] = ema(work["close"], 50)
    work["ema200"] = ema(work["close"], 200)
    work["sma20"] = work["close"].rolling(20, min_periods=20).mean()
    work["std20"] = work["close"].rolling(20, min_periods=20).std(ddof=0)
    work["z20"] = (work["close"] - work["sma20"]) / work["std20"].replace(0, np.nan)
    work["prior_high_6"] = work["high"].rolling(6, min_periods=6).max().shift(1)
    work["prior_low_6"] = work["low"].rolling(6, min_periods=6).min().shift(1)
    work["prior_high_12"] = work["high"].rolling(12, min_periods=12).max().shift(1)
    work["prior_low_12"] = work["low"].rolling(12, min_periods=12).min().shift(1)
    work["prior_high_24"] = work["high"].rolling(24, min_periods=24).max().shift(1)
    work["prior_low_24"] = work["low"].rolling(24, min_periods=24).min().shift(1)
    work["body_atr"] = (work["close"] - work["open"]).abs() / work["atr14"].replace(0, np.nan)
    work["trend_atr"] = (work["ema20"] - work["ema50"]).abs() / work["atr14"].replace(0, np.nan)
    work["atr_ratio"] = work["atr14"] / work["atr14"].rolling(30, min_periods=30).mean()
    work["momentum_3_atr"] = (work["close"] - work["close"].shift(3)) / work["atr14"].replace(0, np.nan)
    work["momentum_6_atr"] = (work["close"] - work["close"].shift(6)) / work["atr14"].replace(0, np.nan)
    work["volume_mean20"] = work["tick_volume"].rolling(20, min_periods=20).mean()
    work["volume_ratio"] = work["tick_volume"] / work["volume_mean20"].replace(0, np.nan)
    work["day"] = work["time"].dt.floor("D")
    work["hour"] = work["time"].dt.hour
    work["weekday"] = work["time"].dt.weekday

    daily = work.groupby("day", as_index=False).agg(
        day_high=("high", "max"),
        day_low=("low", "min"),
    )
    daily["previous_day_high"] = daily["day_high"].shift(1)
    daily["previous_day_low"] = daily["day_low"].shift(1)
    work = work.merge(
        daily[["day", "previous_day_high", "previous_day_low"]],
        on="day",
        how="left",
    )

    if timeframe == "H1":
        h4_bias = completed_bias(h4, 4, "h4")
        work = pd.merge_asof(
            work.sort_values("end"),
            h4_bias.sort_values("available"),
            left_on="end",
            right_on="available",
            direction="backward",
        ).drop(columns=["available"])
    else:
        work["h4_close"] = work["close"]
        work["h4_ema20"] = work["ema20"]
        work["h4_ema50"] = work["ema50"]
        work["h4_atr14"] = work["atr14"]

    d1_bias = completed_bias(d1, 24, "d1")
    work = pd.merge_asof(
        work.sort_values("end"),
        d1_bias.sort_values("available"),
        left_on="end",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    return work.sort_values("time").reset_index(drop=True)


def add_session_range(frame: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    range_rows = frame[(frame["hour"] >= start) & (frame["hour"] < end)]
    ranges = range_rows.groupby("day", as_index=False).agg(
        session_high=("high", "max"),
        session_low=("low", "min"),
        session_bars=("time", "count"),
    )
    return frame.merge(ranges, on="day", how="left")


def bias_masks(frame: pd.DataFrame, spec: AlternativeSpec) -> tuple[pd.Series, pd.Series]:
    long_bias = pd.Series(True, index=frame.index)
    short_bias = pd.Series(True, index=frame.index)
    if spec.require_h4_bias:
        long_bias &= (frame["h4_close"] > frame["h4_ema20"]) & (
            frame["h4_ema20"] > frame["h4_ema50"]
        )
        short_bias &= (frame["h4_close"] < frame["h4_ema20"]) & (
            frame["h4_ema20"] < frame["h4_ema50"]
        )
    if spec.require_d1_bias:
        long_bias &= (frame["d1_close"] > frame["d1_ema20"]) & (
            frame["d1_ema20"] > frame["d1_ema50"]
        )
        short_bias &= (frame["d1_close"] < frame["d1_ema20"]) & (
            frame["d1_ema20"] < frame["d1_ema50"]
        )
    return long_bias, short_bias


def signal_masks(frame: pd.DataFrame, spec: AlternativeSpec) -> tuple[pd.Series, pd.Series]:
    work = (
        add_session_range(frame, spec.range_start, spec.range_end)
        if spec.family in {"ASIA_ORB", "LONDON_ORB"}
        else frame
    )
    long_bias, short_bias = bias_masks(work, spec)
    common = (
        (work["hour"] >= spec.session_start)
        & (work["hour"] < spec.session_end)
        & work["weekday"].isin((0, 1, 2, 3, 4))
        & work["atr14"].notna()
    )

    if spec.family in {"ASIA_ORB", "LONDON_ORB"}:
        required = max(3, spec.range_end - spec.range_start - 1)
        common &= work["session_bars"] >= required
        long_trigger = (
            (work["close"] > work["session_high"])
            & (work["close"] > work["open"])
            & (work["body_atr"] >= spec.threshold)
        )
        short_trigger = (
            (work["close"] < work["session_low"])
            & (work["close"] < work["open"])
            & (work["body_atr"] >= spec.threshold)
        )
    elif spec.family == "PRIOR_DAY_BREAKOUT":
        long_trigger = (
            (work["close"] > work["previous_day_high"])
            & (work["close"] > work["open"])
            & (work["atr_ratio"] >= spec.threshold)
        )
        short_trigger = (
            (work["close"] < work["previous_day_low"])
            & (work["close"] < work["open"])
            & (work["atr_ratio"] >= spec.threshold)
        )
    elif spec.family == "MOMENTUM_BURST":
        momentum = work["momentum_6_atr"] if spec.lookback >= 6 else work["momentum_3_atr"]
        prior_high = work["prior_high_12"] if spec.lookback >= 12 else work["prior_high_6"]
        prior_low = work["prior_low_12"] if spec.lookback >= 12 else work["prior_low_6"]
        long_trigger = (
            (momentum >= spec.threshold)
            & (work["close"] > prior_high)
            & (work["volume_ratio"] >= 1.05)
            & (work["atr_ratio"] >= 0.95)
        )
        short_trigger = (
            (momentum <= -spec.threshold)
            & (work["close"] < prior_low)
            & (work["volume_ratio"] >= 1.05)
            & (work["atr_ratio"] >= 0.95)
        )
    elif spec.family == "H4_TREND_ACCELERATION":
        long_trigger = (
            (work["momentum_3_atr"] >= spec.threshold)
            & (work["ema8"] > work["ema20"])
            & (work["ema20"] > work["ema50"])
            & (work["close"] > work["prior_high_6"])
        )
        short_trigger = (
            (work["momentum_3_atr"] <= -spec.threshold)
            & (work["ema8"] < work["ema20"])
            & (work["ema20"] < work["ema50"])
            & (work["close"] < work["prior_low_6"])
        )
    elif spec.family == "BOLLINGER_FADE":
        low_trend = work["trend_atr"] <= 0.45
        long_trigger = (
            low_trend
            & (work["z20"] <= -spec.threshold)
            & (work["close"] > work["open"])
            & (work["close"] > work["low"] + 0.45 * (work["high"] - work["low"]))
        )
        short_trigger = (
            low_trend
            & (work["z20"] >= spec.threshold)
            & (work["close"] < work["open"])
            & (work["close"] < work["low"] + 0.55 * (work["high"] - work["low"]))
        )
        # Mean reversion deliberately does not require directional trend bias.
        long_bias = pd.Series(True, index=work.index)
        short_bias = pd.Series(True, index=work.index)
    elif spec.family == "RANGE_REENTRY":
        low_trend = work["trend_atr"] <= 0.55
        long_trigger = (
            low_trend
            & (work["low"] < work["previous_day_low"] - 0.10 * work["atr14"])
            & (work["close"] > work["previous_day_low"])
            & (work["close"] > work["open"])
        )
        short_trigger = (
            low_trend
            & (work["high"] > work["previous_day_high"] + 0.10 * work["atr14"])
            & (work["close"] < work["previous_day_high"])
            & (work["close"] < work["open"])
        )
        long_bias = pd.Series(True, index=work.index)
        short_bias = pd.Series(True, index=work.index)
    else:
        raise ValueError(f"Unsupported alternative family: {spec.family}")

    return common & long_bias & long_trigger, common & short_bias & short_trigger


def simulate_exit(
    frame: pd.DataFrame,
    signal_index: int,
    side: int,
    spec: AlternativeSpec,
) -> tuple[pd.Timestamp, float]:
    if signal_index + 1 >= len(frame):
        return pd.Timestamp(frame.iloc[signal_index]["end"]), 0.0
    signal = frame.iloc[signal_index]
    entry_row = frame.iloc[signal_index + 1]
    entry = float(entry_row["open"])
    atr_value = float(signal["atr14"])
    if not np.isfinite(entry) or not np.isfinite(atr_value) or atr_value <= 0:
        return pd.Timestamp(entry_row["end"]), 0.0
    distance = float(spec.stop_atr) * atr_value
    stop = entry - side * distance
    target = entry + side * float(spec.target_r) * distance
    partial = entry + side * float(spec.partial_target_r) * distance
    current_stop = stop
    remaining = 1.0
    realized = 0.0
    partial_taken = False
    last = min(len(frame) - 1, signal_index + int(spec.max_holding_bars))

    for index in range(signal_index + 1, last + 1):
        row = frame.iloc[index]
        low, high = float(row["low"]), float(row["high"])
        stop_hit = low <= current_stop if side > 0 else high >= current_stop
        if stop_hit:
            residual = (current_stop - entry) * side / distance
            return pd.Timestamp(row["end"]), float(realized + remaining * residual)

        if spec.partial_fraction > 0 and not partial_taken:
            partial_hit = high >= partial if side > 0 else low <= partial
            if partial_hit:
                realized += float(spec.partial_fraction) * float(spec.partial_target_r)
                remaining = 1.0 - float(spec.partial_fraction)
                partial_taken = True
                if spec.move_to_break_even:
                    current_stop = entry

        target_hit = high >= target if side > 0 else low <= target
        if target_hit:
            return pd.Timestamp(row["end"]), float(realized + remaining * spec.target_r)

    final = frame.iloc[last]
    residual = (float(final["close"]) - entry) * side / distance
    return pd.Timestamp(final["end"]), float(realized + remaining * residual)


def generate_candidates(
    symbol: str,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    d1: pd.DataFrame,
    spec: AlternativeSpec,
) -> pd.DataFrame:
    source = h4 if spec.timeframe == "H4" else h1
    frame = prepare_frame(source, h4, d1, spec.timeframe)
    long_signal, short_signal = signal_masks(frame, spec)
    rows: list[dict] = []
    last_index = -10_000
    daily_counts: dict[pd.Timestamp, int] = {}
    mask = (long_signal | short_signal).to_numpy()

    for raw_index in np.flatnonzero(mask):
        index = int(raw_index)
        if index + 1 >= len(frame) or index - last_index < spec.cooldown_bars:
            continue
        day = pd.Timestamp(frame.iloc[index]["day"])
        if daily_counts.get(day, 0) >= spec.max_trades_per_day:
            continue
        side = 1 if bool(long_signal.iloc[index]) else -1
        exit_time, result_r = simulate_exit(frame, index, side, spec)
        row = frame.iloc[index]
        rows.append(
            {
                "symbol": symbol.upper(),
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


def alternative_specs(symbol: str) -> tuple[AlternativeSpec, ...]:
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"Unsupported symbol: {symbol}")
    volatility = {"GBPJPY": 1.15, "USDJPY": 1.05}.get(symbol, 1.0)
    asia_end = 7 if symbol in {"AUDUSD", "USDJPY"} else 6
    return (
        AlternativeSpec(
            "ASIA_ORB_TREND",
            "BREAKOUT",
            "ASIA_ORB",
            "H1",
            asia_end,
            12,
            0,
            asia_end,
            threshold=0.14,
            stop_atr=1.00 * volatility,
            target_r=2.0,
            max_holding_bars=10,
            partial_fraction=0.35,
            partial_target_r=1.0,
            move_to_break_even=True,
            require_h4_bias=True,
            cost_r=0.10,
        ),
        AlternativeSpec(
            "LONDON_ORB_NY",
            "BREAKOUT",
            "LONDON_ORB",
            "H1",
            12,
            18,
            6,
            12,
            threshold=0.16,
            stop_atr=1.05 * volatility,
            target_r=2.1,
            max_holding_bars=10,
            partial_fraction=0.35,
            partial_target_r=1.0,
            move_to_break_even=True,
            require_h4_bias=True,
            cost_r=0.10,
        ),
        AlternativeSpec(
            "PRIOR_DAY_EXPANSION",
            "BREAKOUT",
            "PRIOR_DAY_BREAKOUT",
            "H1",
            6,
            18,
            threshold=1.00,
            stop_atr=1.10 * volatility,
            target_r=2.2,
            max_holding_bars=12,
            partial_fraction=0.30,
            partial_target_r=1.1,
            move_to_break_even=True,
            require_h4_bias=True,
            require_d1_bias=True,
            cost_r=0.10,
        ),
        AlternativeSpec(
            "MOMENTUM_BURST_6",
            "MOMENTUM",
            "MOMENTUM_BURST",
            "H1",
            6,
            19,
            lookback=6,
            threshold=0.85,
            stop_atr=0.95 * volatility,
            target_r=1.9,
            max_holding_bars=9,
            partial_fraction=0.40,
            partial_target_r=0.9,
            move_to_break_even=True,
            require_h4_bias=True,
            cost_r=0.10,
        ),
        AlternativeSpec(
            "MOMENTUM_BURST_12",
            "MOMENTUM",
            "MOMENTUM_BURST",
            "H1",
            6,
            19,
            lookback=12,
            threshold=1.10,
            stop_atr=1.05 * volatility,
            target_r=2.2,
            max_holding_bars=12,
            partial_fraction=0.35,
            partial_target_r=1.0,
            move_to_break_even=True,
            require_h4_bias=True,
            require_d1_bias=True,
            cost_r=0.10,
        ),
        AlternativeSpec(
            "H4_TREND_ACCELERATION",
            "MOMENTUM",
            "H4_TREND_ACCELERATION",
            "H4",
            0,
            24,
            lookback=6,
            threshold=0.70,
            stop_atr=1.25 * volatility,
            target_r=2.6,
            max_holding_bars=24,
            partial_fraction=0.30,
            partial_target_r=1.1,
            move_to_break_even=True,
            require_h4_bias=False,
            require_d1_bias=True,
            cost_r=0.05,
            max_trades_per_day=2,
        ),
        AlternativeSpec(
            "BOLLINGER_FADE_18",
            "MEAN_REVERSION",
            "BOLLINGER_FADE",
            "H1",
            6,
            19,
            threshold=1.8,
            stop_atr=1.00 * volatility,
            target_r=1.35,
            max_holding_bars=8,
            require_h4_bias=False,
            require_d1_bias=False,
            cost_r=0.10,
            cooldown_bars=4,
            max_trades_per_day=3,
        ),
        AlternativeSpec(
            "BOLLINGER_FADE_22",
            "MEAN_REVERSION",
            "BOLLINGER_FADE",
            "H1",
            6,
            19,
            threshold=2.2,
            stop_atr=1.10 * volatility,
            target_r=1.50,
            max_holding_bars=10,
            require_h4_bias=False,
            require_d1_bias=False,
            cost_r=0.10,
            cooldown_bars=5,
            max_trades_per_day=2,
        ),
        AlternativeSpec(
            "PREVIOUS_DAY_REENTRY",
            "MEAN_REVERSION",
            "RANGE_REENTRY",
            "H1",
            6,
            18,
            threshold=1.0,
            stop_atr=0.95 * volatility,
            target_r=1.45,
            max_holding_bars=8,
            require_h4_bias=False,
            require_d1_bias=False,
            cost_r=0.10,
            cooldown_bars=4,
            max_trades_per_day=3,
        ),
    )


def generate_symbol_candidates(
    symbol: str,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    d1: pd.DataFrame,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for spec in alternative_specs(symbol):
        generated = generate_candidates(symbol, h1, h4, d1, spec)
        if not generated.empty:
            frames.append(generated)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    return output.sort_values(["entry_time", "symbol", "mode", "profile"]).reset_index(drop=True)


def validate_specs(specs: Iterable[AlternativeSpec]) -> None:
    for spec in specs:
        if spec.mode not in {"BREAKOUT", "MOMENTUM", "MEAN_REVERSION"}:
            raise RuntimeError(f"Invalid alternative mode: {spec.mode}")
        if spec.timeframe not in {"H1", "H4"}:
            raise RuntimeError(f"Invalid timeframe: {spec.timeframe}")
        if not 0 <= spec.session_start < spec.session_end <= 24:
            raise RuntimeError(f"Invalid session: {spec.name}")
        if spec.stop_atr <= 0 or spec.target_r <= 0:
            raise RuntimeError(f"Invalid risk geometry: {spec.name}")
        if not 0 <= spec.partial_fraction < 1:
            raise RuntimeError(f"Invalid partial fraction: {spec.name}")
        if spec.cost_r < 0:
            raise RuntimeError(f"Invalid cost allowance: {spec.name}")


for _symbol in SYMBOLS:
    validate_specs(alternative_specs(_symbol))
