"""V15 research-only diversified strategy families.

Signals use completed H4/D1 candles and enter on the next H4 open during the
London/New York trading window. Bid/ask candles are used directly so spread is
embedded in each raw R multiple. No MT5 or broker API is imported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

CORE_SYMBOLS = {"GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"}
ENTRY_HOURS_UTC = {8, 12, 16}


@dataclass(frozen=True)
class StrategySpec:
    name: str
    family: str
    signal_scope: str  # H4 or D1
    lookback: int
    stop_atr: float
    target_r: float | None
    max_holding_h4_bars: int
    trailing_h4_bars: int
    minimum_body_atr: float = 0.0
    minimum_atr_ratio: float = 0.0
    slippage_reserve_r: float = 0.02


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    tr = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high_diff = frame["high"].diff()
    low_diff = -frame["low"].diff()
    plus_dm = pd.Series(np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0), index=frame.index)
    tr = atr(frame, period)
    plus_di = 100.0 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def resample_ohlc(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    work = frame.copy()
    work["time"] = pd.to_datetime(work["time"], utc=True)
    indexed = work.set_index("time")
    output = indexed.resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
    )
    return output.dropna(subset=["open", "high", "low", "close"]).reset_index()


def _daily_features(d1_bid: pd.DataFrame) -> pd.DataFrame:
    work = d1_bid.copy().sort_values("time").reset_index(drop=True)
    work["d1_atr14"] = atr(work)
    work["d1_ema20"] = ema(work["close"], 20)
    work["d1_ema50"] = ema(work["close"], 50)
    work["d1_ema200"] = ema(work["close"], 200)
    work["d1_adx14"] = adx(work)
    work["d1_return_63"] = work["close"].pct_change(63)
    work["d1_return_126"] = work["close"].pct_change(126)
    work["d1_vol20"] = work["close"].pct_change().rolling(20, min_periods=20).std() * np.sqrt(252.0)
    work["d1_high20"] = work["high"].rolling(20, min_periods=20).max().shift(1)
    work["d1_low20"] = work["low"].rolling(20, min_periods=20).min().shift(1)
    work["d1_high60"] = work["high"].rolling(60, min_periods=60).max().shift(1)
    work["d1_low60"] = work["low"].rolling(60, min_periods=60).min().shift(1)
    work["d1_high120"] = work["high"].rolling(120, min_periods=120).max().shift(1)
    work["d1_low120"] = work["low"].rolling(120, min_periods=120).min().shift(1)
    rolling_mean = work["close"].rolling(20, min_periods=20).mean()
    rolling_std = work["close"].rolling(20, min_periods=20).std().replace(0, np.nan)
    work["d1_z20"] = (work["close"] - rolling_mean) / rolling_std
    bandwidth = (4.0 * rolling_std) / rolling_mean.replace(0, np.nan)
    work["d1_squeeze"] = bandwidth < bandwidth.rolling(252, min_periods=126).quantile(0.20).shift(1)
    work["d1_close"] = work["close"]
    work["available"] = work["time"] + pd.Timedelta(days=1)
    columns = [column for column in work.columns if column.startswith("d1_")]
    return work[["available", *columns]].sort_values("available")


def prepare_execution_frame(h1_bid: pd.DataFrame, h1_ask: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    h4_bid = resample_ohlc(h1_bid, "4h")
    h4_ask = resample_ohlc(h1_ask, "4h")
    d1_bid = resample_ohlc(h1_bid, "1D")

    bid = h4_bid.rename(columns={column: f"bid_{column}" for column in ("open", "high", "low", "close", "tick_volume")})
    ask = h4_ask.rename(columns={column: f"ask_{column}" for column in ("open", "high", "low", "close", "tick_volume")})
    frame = bid.merge(ask, on="time", how="inner").sort_values("time").reset_index(drop=True)
    frame["end"] = frame["time"] + pd.Timedelta(hours=4)

    signal = pd.DataFrame(
        {
            "time": frame["time"],
            "open": frame["bid_open"],
            "high": frame["bid_high"],
            "low": frame["bid_low"],
            "close": frame["bid_close"],
            "tick_volume": frame["bid_tick_volume"],
        }
    )
    frame["h4_atr14"] = atr(signal)
    frame["h4_ema10"] = ema(signal["close"], 10)
    frame["h4_ema20"] = ema(signal["close"], 20)
    frame["h4_ema50"] = ema(signal["close"], 50)
    frame["h4_ema100"] = ema(signal["close"], 100)
    frame["h4_prior_high20"] = signal["high"].rolling(20, min_periods=20).max().shift(1)
    frame["h4_prior_low20"] = signal["low"].rolling(20, min_periods=20).min().shift(1)
    frame["h4_prior_high40"] = signal["high"].rolling(40, min_periods=40).max().shift(1)
    frame["h4_prior_low40"] = signal["low"].rolling(40, min_periods=40).min().shift(1)
    frame["h4_prior_high80"] = signal["high"].rolling(80, min_periods=80).max().shift(1)
    frame["h4_prior_low80"] = signal["low"].rolling(80, min_periods=80).min().shift(1)
    frame["h4_body_atr"] = (signal["close"] - signal["open"]).abs() / frame["h4_atr14"].replace(0, np.nan)
    frame["h4_atr_ratio"] = frame["h4_atr14"] / frame["h4_atr14"].rolling(60, min_periods=30).mean()
    frame["previous_high"] = signal["high"].shift(1)
    frame["previous_low"] = signal["low"].shift(1)
    frame["previous_close"] = signal["close"].shift(1)

    daily = _daily_features(d1_bid)
    frame = pd.merge_asof(
        frame.sort_values("end"),
        daily,
        left_on="end",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    frame["next_entry_hour"] = (frame["time"].dt.hour + 4) % 24
    frame["weekday"] = frame["time"].dt.weekday
    return frame.reset_index(drop=True), d1_bid


def strategy_specs() -> tuple[StrategySpec, ...]:
    return (
        StrategySpec("H4_BREAKOUT_40", "H4_BREAKOUT", "H4", 40, 1.8, 3.0, 72, 20, 0.18, 0.90, 0.025),
        StrategySpec("H4_BREAKOUT_80", "H4_BREAKOUT", "H4", 80, 2.0, 3.6, 120, 30, 0.14, 0.85, 0.025),
        StrategySpec("H4_PULLBACK_20", "H4_PULLBACK", "H4", 20, 1.6, 2.6, 60, 16, 0.10, 0.80, 0.025),
        StrategySpec("H4_VOL_EXPANSION_24", "H4_VOL_EXPANSION", "H4", 20, 1.9, 3.2, 84, 24, 0.22, 1.08, 0.030),
        StrategySpec("D1_DONCHIAN_60", "D1_TREND", "D1", 60, 2.2, None, 240, 60, 0.0, 0.0, 0.020),
        StrategySpec("D1_DONCHIAN_120", "D1_TREND", "D1", 120, 2.6, None, 360, 120, 0.0, 0.0, 0.020),
        StrategySpec("D1_SQUEEZE_20", "D1_SQUEEZE", "D1", 20, 2.0, 3.2, 180, 60, 0.0, 0.0, 0.025),
        StrategySpec("D1_RANGE_REVERSION", "D1_REVERSION", "D1", 20, 2.2, 1.6, 60, 0, 0.0, 0.0, 0.025),
    )


def signal_masks(frame: pd.DataFrame, spec: StrategySpec) -> tuple[pd.Series, pd.Series]:
    next_session = frame["next_entry_hour"].isin(ENTRY_HOURS_UTC)
    weekday = frame["weekday"].isin((0, 1, 2, 3, 4))
    h4_long_trend = (frame["h4_ema20"] > frame["h4_ema50"]) & (frame["h4_ema50"] > frame["h4_ema100"])
    h4_short_trend = (frame["h4_ema20"] < frame["h4_ema50"]) & (frame["h4_ema50"] < frame["h4_ema100"])
    d1_long_trend = (frame["d1_close"] > frame["d1_ema50"]) & (frame["d1_ema50"] > frame["d1_ema200"])
    d1_short_trend = (frame["d1_close"] < frame["d1_ema50"]) & (frame["d1_ema50"] < frame["d1_ema200"])

    if spec.signal_scope == "D1":
        common = next_session & weekday & (frame["time"].dt.hour == 4) & frame["d1_atr14"].notna()
    else:
        common = (
            next_session
            & weekday
            & frame["h4_atr14"].notna()
            & (frame["h4_body_atr"] >= spec.minimum_body_atr)
            & (frame["h4_atr_ratio"] >= spec.minimum_atr_ratio)
        )

    if spec.name == "H4_BREAKOUT_40":
        long_signal = h4_long_trend & d1_long_trend & (frame["bid_close"] > frame["h4_prior_high40"])
        short_signal = h4_short_trend & d1_short_trend & (frame["bid_close"] < frame["h4_prior_low40"])
    elif spec.name == "H4_BREAKOUT_80":
        long_signal = d1_long_trend & (frame["bid_close"] > frame["h4_prior_high80"])
        short_signal = d1_short_trend & (frame["bid_close"] < frame["h4_prior_low80"])
    elif spec.name == "H4_PULLBACK_20":
        long_signal = (
            h4_long_trend & d1_long_trend
            & (frame["bid_low"] <= frame["h4_ema20"])
            & (frame["bid_close"] > frame["h4_ema10"])
            & (frame["bid_close"] > frame["previous_high"])
        )
        short_signal = (
            h4_short_trend & d1_short_trend
            & (frame["bid_high"] >= frame["h4_ema20"])
            & (frame["bid_close"] < frame["h4_ema10"])
            & (frame["bid_close"] < frame["previous_low"])
        )
    elif spec.name == "H4_VOL_EXPANSION_24":
        long_signal = d1_long_trend & (frame["bid_close"] > frame["h4_prior_high20"])
        short_signal = d1_short_trend & (frame["bid_close"] < frame["h4_prior_low20"])
    elif spec.name == "D1_DONCHIAN_60":
        long_signal = d1_long_trend & (frame["d1_close"] > frame["d1_high60"])
        short_signal = d1_short_trend & (frame["d1_close"] < frame["d1_low60"])
    elif spec.name == "D1_DONCHIAN_120":
        long_signal = d1_long_trend & (frame["d1_close"] > frame["d1_high120"])
        short_signal = d1_short_trend & (frame["d1_close"] < frame["d1_low120"])
    elif spec.name == "D1_SQUEEZE_20":
        long_signal = frame["d1_squeeze"].fillna(False) & d1_long_trend & (frame["d1_close"] > frame["d1_high20"])
        short_signal = frame["d1_squeeze"].fillna(False) & d1_short_trend & (frame["d1_close"] < frame["d1_low20"])
    elif spec.name == "D1_RANGE_REVERSION":
        quiet = frame["d1_adx14"] < 18.0
        long_signal = quiet & (frame["d1_z20"] <= -2.20) & (frame["d1_close"] > frame["d1_ema200"] * 0.92)
        short_signal = quiet & (frame["d1_z20"] >= 2.20) & (frame["d1_close"] < frame["d1_ema200"] * 1.08)
    else:  # pragma: no cover - specification guard
        raise ValueError(f"Unsupported V15 strategy: {spec.name}")
    return common & long_signal, common & short_signal


def _trail_levels(frame: pd.DataFrame, lookback: int) -> tuple[pd.Series, pd.Series]:
    if lookback <= 0:
        empty = pd.Series(np.nan, index=frame.index)
        return empty, empty
    long_level = frame["bid_low"].rolling(lookback, min_periods=lookback).min().shift(1)
    short_level = frame["ask_high"].rolling(lookback, min_periods=lookback).max().shift(1)
    return long_level, short_level


def simulate_exit(
    frame: pd.DataFrame,
    signal_index: int,
    side: int,
    stop_distance: float,
    spec: StrategySpec,
) -> tuple[pd.Timestamp, float]:
    entry_index = signal_index + 1
    if entry_index >= len(frame) or not np.isfinite(stop_distance) or stop_distance <= 0:
        return pd.Timestamp(frame.iloc[signal_index]["end"]), 0.0

    entry_row = frame.iloc[entry_index]
    entry = float(entry_row["ask_open"] if side > 0 else entry_row["bid_open"])
    stop = entry - side * stop_distance
    target = None if spec.target_r is None else entry + side * spec.target_r * stop_distance
    long_trail, short_trail = _trail_levels(frame, spec.trailing_h4_bars)
    last_index = min(len(frame) - 1, entry_index + spec.max_holding_h4_bars - 1)

    for index in range(entry_index, last_index + 1):
        row = frame.iloc[index]
        if side > 0:
            market_open = float(row["bid_open"])
            market_low = float(row["bid_low"])
            market_high = float(row["bid_high"])
            if market_open <= stop:
                return pd.Timestamp(row["end"]), float((market_open - entry) / stop_distance)
            if market_low <= stop:
                return pd.Timestamp(row["end"]), -1.0
            trail = float(long_trail.iloc[index]) if np.isfinite(long_trail.iloc[index]) else np.nan
            if np.isfinite(trail) and trail > stop and market_low <= trail:
                return pd.Timestamp(row["end"]), float((trail - entry) / stop_distance)
            if target is not None and market_high >= target:
                return pd.Timestamp(row["end"]), float(spec.target_r)
        else:
            market_open = float(row["ask_open"])
            market_high = float(row["ask_high"])
            market_low = float(row["ask_low"])
            if market_open >= stop:
                return pd.Timestamp(row["end"]), float((entry - market_open) / stop_distance)
            if market_high >= stop:
                return pd.Timestamp(row["end"]), -1.0
            trail = float(short_trail.iloc[index]) if np.isfinite(short_trail.iloc[index]) else np.nan
            if np.isfinite(trail) and trail < stop and market_high >= trail:
                return pd.Timestamp(row["end"]), float((entry - trail) / stop_distance)
            if target is not None and market_low <= target:
                return pd.Timestamp(row["end"]), float(spec.target_r)

    final = frame.iloc[last_index]
    exit_price = float(final["bid_close"] if side > 0 else final["ask_close"])
    return pd.Timestamp(final["end"]), float((exit_price - entry) * side / stop_distance)


def generate_candidates_from_frame(
    symbol: str,
    frame: pd.DataFrame,
    spec: StrategySpec,
) -> pd.DataFrame:
    long_signal, short_signal = signal_masks(frame, spec)
    rows: list[dict] = []
    next_available = pd.Timestamp.min.tz_localize("UTC")

    for raw_index in np.flatnonzero((long_signal | short_signal).to_numpy()):
        index = int(raw_index)
        if index + 1 >= len(frame):
            continue
        entry_time = pd.Timestamp(frame.iloc[index + 1]["time"])
        if entry_time < next_available:
            continue
        side = 1 if bool(long_signal.iloc[index]) else -1
        atr_value = float(frame.iloc[index]["d1_atr14"] if spec.signal_scope == "D1" else frame.iloc[index]["h4_atr14"])
        stop_distance = spec.stop_atr * atr_value
        exit_time, raw_r = simulate_exit(frame, index, side, stop_distance, spec)
        if not np.isfinite(raw_r):
            continue
        rows.append(
            {
                "symbol": symbol,
                "mode": "DIVERSIFIED",
                "engine": f"{symbol}_V15_{spec.name}".upper(),
                "family": spec.family,
                "profile": spec.name,
                "timeframe": spec.signal_scope,
                "side": "BUY" if side > 0 else "SELL",
                "entry_time": entry_time,
                "exit_time": exit_time,
                "r_multiple": float(raw_r),
                "raw_r_multiple": float(raw_r),
                "selection_cost_r": float(spec.slippage_reserve_r),
                "cost_r": float(spec.slippage_reserve_r),
                "stop_atr": float(spec.stop_atr),
                "target_r": spec.target_r,
                "strategy_group": "V15_DIVERSIFIED",
            }
        )
        next_available = exit_time
    return pd.DataFrame(rows)


def generate_candidates(
    symbol: str,
    h1_bid: pd.DataFrame,
    h1_ask: pd.DataFrame,
    spec: StrategySpec,
) -> pd.DataFrame:
    frame, _ = prepare_execution_frame(h1_bid, h1_ask)
    return generate_candidates_from_frame(symbol, frame, spec)


def generate_cross_sectional_candidates(
    market: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    executable_symbols: Iterable[str],
) -> pd.DataFrame:
    daily: dict[str, pd.DataFrame] = {}
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, (h1_bid, h1_ask) in market.items():
        frame, d1 = prepare_execution_frame(h1_bid, h1_ask)
        prepared[symbol] = frame
        work = d1.copy().sort_values("time").reset_index(drop=True)
        work["ret63"] = work["close"].pct_change(63)
        work["ret126"] = work["close"].pct_change(126)
        work["vol20"] = work["close"].pct_change().rolling(20, min_periods=20).std() * np.sqrt(252.0)
        work["ema200"] = ema(work["close"], 200)
        work["atr14"] = atr(work)
        work["score"] = (0.60 * work["ret63"] + 0.40 * work["ret126"]) / work["vol20"].replace(0, np.nan)
        work["available"] = work["time"] + pd.Timedelta(days=1)
        daily[symbol] = work[["available", "close", "ema200", "atr14", "score"]]

    score_frames = []
    for symbol, frame in daily.items():
        temp = frame.copy()
        temp["symbol"] = symbol
        score_frames.append(temp)
    panel = pd.concat(score_frames, ignore_index=True, sort=False).dropna(subset=["score", "atr14"])
    panel["week"] = panel["available"].dt.to_period("W-SUN").astype(str)
    first_days = panel.groupby(["week", "symbol"], as_index=False)["available"].min()
    panel = panel.merge(first_days, on=["week", "symbol", "available"], how="inner")

    rows: list[dict] = []
    unavailable_until: dict[str, pd.Timestamp] = {
        symbol: pd.Timestamp.min.tz_localize("UTC") for symbol in market
    }
    allowed = set(executable_symbols)
    spec = StrategySpec("CROSS_SECTIONAL_63_126", "CROSS_SECTIONAL", "D1", 126, 2.4, None, 180, 60, slippage_reserve_r=0.025)

    for available, group in panel.groupby("available", sort=True):
        eligible = group[group["symbol"].isin(allowed)].sort_values("score")
        if len(eligible) < 8:
            continue
        count = max(1, int(np.ceil(len(eligible) * 0.15)))
        selected = pd.concat([eligible.head(count), eligible.tail(count)], ignore_index=True)
        for row in selected.itertuples(index=False):
            side = 1 if float(row.score) > 0 else -1
            if side > 0 and not float(row.close) > float(row.ema200):
                continue
            if side < 0 and not float(row.close) < float(row.ema200):
                continue
            symbol = str(row.symbol)
            frame = prepared[symbol]
            candidates = frame[(frame["time"] >= pd.Timestamp(available)) & (frame["time"].dt.hour == 8)]
            if candidates.empty:
                continue
            entry_index = int(candidates.index[0])
            if entry_index <= 0:
                continue
            entry_time = pd.Timestamp(frame.loc[entry_index, "time"])
            if entry_time < unavailable_until[symbol]:
                continue
            signal_index = entry_index - 1
            stop_distance = 2.4 * float(row.atr14)
            exit_time, raw_r = simulate_exit(frame, signal_index, side, stop_distance, spec)
            if not np.isfinite(raw_r):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "mode": "DIVERSIFIED",
                    "engine": f"{symbol}_V15_CROSS_SECTIONAL_63_126",
                    "family": "CROSS_SECTIONAL",
                    "profile": "CROSS_SECTIONAL_63_126",
                    "timeframe": "D1",
                    "side": "BUY" if side > 0 else "SELL",
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "r_multiple": float(raw_r),
                    "raw_r_multiple": float(raw_r),
                    "selection_cost_r": 0.025,
                    "cost_r": 0.025,
                    "stop_atr": 2.4,
                    "target_r": None,
                    "strategy_group": "V15_CROSS_SECTIONAL",
                    "cross_sectional_score": float(row.score),
                }
            )
            unavailable_until[symbol] = exit_time
    return pd.DataFrame(rows)


def generate_universe_candidates(
    market: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    executable_symbols: Iterable[str],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    executable = set(executable_symbols)
    for symbol in sorted(executable):
        h1_bid, h1_ask = market[symbol]
        prepared_frame, _ = prepare_execution_frame(h1_bid, h1_ask)
        for spec in strategy_specs():
            generated = generate_candidates_from_frame(symbol, prepared_frame, spec)
            if not generated.empty:
                frames.append(generated)
    cross = generate_cross_sectional_candidates(market, executable)
    if not cross.empty:
        frames.append(cross)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True, sort=False)
    output["entry_time"] = pd.to_datetime(output["entry_time"], utc=True)
    output["exit_time"] = pd.to_datetime(output["exit_time"], utc=True)
    output["r_multiple"] = pd.to_numeric(output["raw_r_multiple"], errors="coerce") - pd.to_numeric(output["selection_cost_r"], errors="coerce")
    output = output.dropna(subset=["entry_time", "exit_time", "r_multiple"])
    return output.sort_values(["entry_time", "symbol", "family", "profile"]).drop_duplicates(
        ["entry_time", "exit_time", "symbol", "family", "profile", "side"]
    ).reset_index(drop=True)
