"""Satellite V4 research models.

EURUSD V4 is long-only, late-London, pullback-resumption only.
GBPJPY V4 is long-only, London breakout followed by a mandatory retest.
Both remain disabled until promotion gates pass.
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
class SatelliteV4Signal:
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
class EURUSDV4Params:
    enabled: bool = False
    risk_percent: float = 0.15
    h1_adx_min: float = 18.0
    m30_atr_pct_min: float = 0.25
    m30_atr_pct_max: float = 0.85
    body_min: float = 0.45
    volume_ratio_min: float = 1.0
    rsi_min: float = 50.0
    rsi_max: float = 67.0
    stop_atr: float = 1.15
    target_r: float = 2.20
    partial_fraction: float = 0.50
    partial_r: float = 1.0
    trail_atr: float = 1.50
    max_hold_m15_bars: int = 32


@dataclass(frozen=True)
class GBPJPYV4Params:
    enabled: bool = False
    risk_percent: float = 0.10
    h1_adx_min: float = 22.0
    h1_atr_pct_min: float = 0.65
    breakout_lookback_m30: int = 8
    body_min: float = 0.35
    volume_ratio_min: float = 1.0
    rsi_min: float = 54.0
    retest_tolerance_atr: float = 0.20
    stop_atr: float = 1.40
    target_r: float = 2.80
    partial_fraction: float = 0.40
    partial_r: float = 1.20
    trail_atr: float = 2.0
    max_hold_m15_bars: int = 64


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
    result["ema20_slope"] = result["ema20"].diff(3)
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


def evaluate_eurusd_v4(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: EURUSDV4Params = EURUSDV4Params(),
) -> Optional[SatelliteV4Signal]:
    if not params.enabled or min(len(m15), len(m30), len(h1)) < 220:
        return None
    m15f, m30f, h1f = add_features(m15), add_features(m30), add_features(h1)
    row, context, anchor = m15f.iloc[-1], m30f.iloc[-1], h1f.iloc[-1]
    signal_time = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = signal_time.hour + signal_time.minute / 60
    if signal_time.weekday() >= 5 or not 10 <= hour < 12:
        return None
    regime = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= params.h1_adx_min
        and anchor["ema20_slope"] > 0
        and context["ema20"] > context["ema50"]
        and context["close"] > context["ema20"]
        and params.m30_atr_pct_min <= context["atr_percentile"] <= params.m30_atr_pct_max
    )
    if not regime:
        return None
    recent = m15f.iloc[-7:-1]
    pullback = bool(
        ((recent["low"] <= recent["ema20"] + 0.10 * recent["atr14"])
         | (recent["close"] < recent["ema20"])).any()
    )
    trigger = (
        pullback
        and row["close"] > row["ema20"]
        and row["close"] > row["open"]
        and row["body_ratio"] >= params.body_min
        and row["volume_ratio"] >= params.volume_ratio_min
        and params.rsi_min <= row["rsi14"] <= params.rsi_max
        and row["close"] > m15f["high"].iloc[-3:-1].max()
    )
    if not trigger:
        return None
    return SatelliteV4Signal(
        "EURUSD", Signal.BUY, "EURUSD_LATE_LONDON_PULLBACK_V4",
        signal_time.to_pydatetime(), float(row["atr14"]),
        params.stop_atr, params.target_r, params.risk_percent,
        params.partial_fraction, params.partial_r, params.trail_atr,
        params.max_hold_m15_bars,
        "H1/M30 bullish regime with late-London M15 pullback resumption.",
    )


def evaluate_gbpjpy_v4(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: GBPJPYV4Params = GBPJPYV4Params(),
) -> Optional[SatelliteV4Signal]:
    if not params.enabled or min(len(m15), len(m30), len(h1)) < 220:
        return None
    m15f, m30f, h1f = add_features(m15), add_features(m30), add_features(h1)
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    signal_time = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = signal_time.hour + signal_time.minute / 60
    if signal_time.weekday() >= 5 or not 7 <= hour < 12.5:
        return None
    regime = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= params.h1_adx_min
        and anchor["atr_percentile"] >= params.h1_atr_pct_min
        and anchor["ema20_slope"] > 0
    )
    if not regime:
        return None
    completed = m30f.iloc[:-1]
    level = completed["high"].shift(1).rolling(params.breakout_lookback_m30).max()
    breakout_rows = completed[completed["close"] > level].tail(4)
    if breakout_rows.empty:
        return None
    breakout_level = float(level.loc[breakout_rows.index[-1]])
    trigger = (
        row["low"] <= breakout_level + params.retest_tolerance_atr * row["atr14"]
        and row["close"] > breakout_level
        and row["close"] > row["open"]
        and row["body_ratio"] >= params.body_min
        and row["volume_ratio"] >= params.volume_ratio_min
        and row["rsi14"] >= params.rsi_min
    )
    if not trigger:
        return None
    return SatelliteV4Signal(
        "GBPJPY", Signal.BUY, "GBPJPY_LONDON_BREAKOUT_RETEST_V4",
        signal_time.to_pydatetime(), float(row["atr14"]),
        params.stop_atr, params.target_r, params.risk_percent,
        params.partial_fraction, params.partial_r, params.trail_atr,
        params.max_hold_m15_bars,
        "H1 volatility/trend regime with mandatory M30 breakout and M15 retest.",
    )
