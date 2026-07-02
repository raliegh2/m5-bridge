"""Satellite V3 research models.

GBPUSD V4 remains unchanged. GBPUSD Satellite V2 remains the existing London
M15 research engine. EURUSD V3 is long-only with H1 bias and M15 pullback or
breakout-retest entries. GBPJPY V3 uses H1 trend/volatility, M30 breakout and
M15 retest entries during London.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from .enums import Signal
from .gbpusd_v4 import _adx, _atr, _ema


@dataclass(frozen=True)
class SatelliteV3Signal:
    symbol: str
    side: Signal
    setup: str
    signal_time: datetime
    atr_price: float
    stop_atr: float
    target_r: float
    risk_percent: float
    partial_fraction: float
    partial_r: float
    trail_atr: float
    max_hold_m15_bars: int
    reason: str


@dataclass(frozen=True)
class EURUSDV3Params:
    enabled: bool = False
    risk_percent: float = 0.15
    h1_adx_min: float = 18.0
    h1_atr_percentile_min: float = 0.15
    h1_atr_percentile_max: float = 0.90
    pullback_stop_atr: float = 1.15
    pullback_target_r: float = 2.0
    retest_stop_atr: float = 1.10
    retest_target_r: float = 2.20
    partial_fraction: float = 0.50
    partial_r: float = 1.0
    trail_atr: float = 1.50
    max_hold_m15_bars: int = 96


@dataclass(frozen=True)
class GBPJPYV3Params:
    enabled: bool = False
    risk_percent: float = 0.10
    h1_adx_min: float = 22.0
    h1_atr_percentile_min: float = 0.45
    stop_atr: float = 1.55
    target_r: float = 2.50
    partial_fraction: float = 0.40
    partial_r: float = 1.20
    trail_atr: float = 2.0
    max_hold_m15_bars: int = 128


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["atr14"] = _atr(result, 14)
    result["rsi14"] = _rsi(result["close"], 14)
    result["ema20"] = _ema(result["close"], 20)
    result["ema50"] = _ema(result["close"], 50)
    result["adx14"] = _adx(result, 14)
    result["body_ratio"] = (
        (result["close"] - result["open"]).abs()
        / (result["high"] - result["low"]).replace(0, np.nan)
    )
    average_volume = result["tick_volume"].rolling(20, min_periods=20).mean()
    result["volume_ratio"] = result["tick_volume"] / average_volume
    result["atr_percentile"] = result["atr14"].rolling(
        500, min_periods=200
    ).rank(pct=True)
    return result


def evaluate_eurusd_v3(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: EURUSDV3Params = EURUSDV3Params(),
) -> Optional[SatelliteV3Signal]:
    if not params.enabled or min(len(m15), len(m30), len(h1)) < 220:
        return None
    m15f, h1f = add_features(m15), add_features(h1)
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    signal_time = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = signal_time.hour + signal_time.minute / 60
    if signal_time.weekday() >= 5 or not 7 <= hour < 16:
        return None
    bias = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= params.h1_adx_min
        and params.h1_atr_percentile_min
        <= anchor["atr_percentile"]
        <= params.h1_atr_percentile_max
    )
    if not bias:
        return None

    recent = m15f.iloc[-7:-1]
    touched = bool(
        ((recent["low"] <= recent["ema20"] + 0.10 * recent["atr14"])
         | (recent["close"] < recent["ema20"])).any()
    )
    pullback = (
        7 <= hour < 12
        and touched
        and row["close"] > row["ema20"]
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.35
        and row["volume_ratio"] >= 0.85
        and 50 <= row["rsi14"] <= 67
        and row["close"] > m15f["high"].iloc[-3:-1].max()
    )
    if pullback:
        return SatelliteV3Signal(
            "EURUSD", Signal.BUY, "EURUSD_LONDON_PULLBACK_V3",
            signal_time.to_pydatetime(), float(row["atr14"]),
            params.pullback_stop_atr, params.pullback_target_r,
            params.risk_percent, params.partial_fraction, params.partial_r,
            params.trail_atr, params.max_hold_m15_bars,
            "H1 long bias with London M15 pullback and momentum resumption.",
        )

    prior_level = m15f["high"].iloc[-33:-9].max()
    broke = bool((m15f["close"].iloc[-9:-1] > prior_level).any())
    retest = (
        9 <= hour < 16
        and broke
        and row["low"] <= prior_level + 0.12 * row["atr14"]
        and row["close"] > prior_level
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.30
        and row["volume_ratio"] >= 0.85
        and 52 <= row["rsi14"] <= 69
    )
    if retest:
        return SatelliteV3Signal(
            "EURUSD", Signal.BUY, "EURUSD_BREAKOUT_RETEST_V3",
            signal_time.to_pydatetime(), float(row["atr14"]),
            params.retest_stop_atr, params.retest_target_r,
            params.risk_percent, params.partial_fraction, params.partial_r,
            params.trail_atr, params.max_hold_m15_bars,
            "H1 long bias with breakout followed by an M15 retest hold.",
        )
    return None


def evaluate_gbpjpy_v3(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: GBPJPYV3Params = GBPJPYV3Params(),
) -> Optional[SatelliteV3Signal]:
    if not params.enabled or min(len(m15), len(m30), len(h1)) < 220:
        return None
    m15f, h1f = add_features(m15), add_features(h1)
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    signal_time = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = signal_time.hour + signal_time.minute / 60
    if signal_time.weekday() >= 5 or not 7 <= hour < 13:
        return None
    long_bias = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= params.h1_adx_min
        and anchor["atr_percentile"] >= params.h1_atr_percentile_min
    )
    short_bias = (
        anchor["ema20"] < anchor["ema50"]
        and anchor["close"] < anchor["ema20"]
        and anchor["adx14"] >= params.h1_adx_min
        and anchor["atr_percentile"] >= params.h1_atr_percentile_min
    )
    base = m15f.iloc[-39:-7]
    recent = m15f.iloc[-7:-1]
    if len(base) < 32:
        return None
    upper, lower = base["high"].max(), base["low"].min()
    broke_up = bool((recent["close"] > upper).any())
    broke_down = bool((recent["close"] < lower).any())
    tolerance = 0.15 * row["atr14"]
    long_trigger = (
        long_bias and broke_up
        and row["low"] <= upper + tolerance
        and row["close"] > upper
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.40
        and row["volume_ratio"] >= 0.90
        and row["rsi14"] >= 54
    )
    short_trigger = (
        short_bias and broke_down
        and row["high"] >= lower - tolerance
        and row["close"] < lower
        and row["close"] < row["open"]
        and row["body_ratio"] >= 0.40
        and row["volume_ratio"] >= 0.90
        and row["rsi14"] <= 46
    )
    if not long_trigger and not short_trigger:
        return None
    return SatelliteV3Signal(
        "GBPJPY", Signal.BUY if long_trigger else Signal.SELL,
        "GBPJPY_LONDON_BREAKOUT_RETEST_V3",
        signal_time.to_pydatetime(), float(row["atr14"]),
        params.stop_atr, params.target_r, params.risk_percent,
        params.partial_fraction, params.partial_r, params.trail_atr,
        params.max_hold_m15_bars,
        "H1 trend/volatility regime with London breakout and M15 retest.",
    )
