"""Higher-frequency EURUSD and GBPJPY satellite engines.

These models are disabled until walk-forward and forward-test promotion gates pass.
GBPUSD remains delegated to the frozen V4 swing engine.
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
class SatelliteRisk:
    risk_percent: float
    partial_fraction: float
    partial_r: float
    trail_atr: float
    max_hold_h4_bars: int


@dataclass(frozen=True)
class EURUSDSatelliteParams:
    enabled: bool = False
    long_enabled: bool = True
    short_enabled: bool = True
    d1_adx_min: float = 16.0
    h4_adx_min: float = 14.0
    atr_pct_min: float = 0.20
    atr_pct_max: float = 0.90
    pullback_stop_atr: float = 0.95
    breakout_stop_atr: float = 1.10
    target_r: float = 2.20
    body_pullback_min: float = 0.25
    body_breakout_min: float = 0.40
    breakout_lookback: int = 6
    risk: SatelliteRisk = SatelliteRisk(0.20, 0.50, 1.50, 1.60, 30)


@dataclass(frozen=True)
class GBPJPYSatelliteParams:
    enabled: bool = False
    long_enabled: bool = True
    short_enabled: bool = True
    d1_adx_min: float = 20.0
    h4_adx_min: float = 24.0
    d1_atr_pct_min: float = 0.40
    breakout_stop_atr: float = 1.55
    pullback_stop_atr: float = 1.70
    target_r: float = 2.60
    body_breakout_min: float = 0.45
    body_pullback_min: float = 0.35
    breakout_lookback: int = 8
    oversized_candle_atr: float = 2.20
    risk: SatelliteRisk = SatelliteRisk(0.15, 0.40, 1.80, 2.00, 42)


@dataclass(frozen=True)
class SatelliteSignal:
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
    max_hold_h4_bars: int
    reason: str


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["ema21"] = _ema(result["close"], 21)
    result["ema50"] = _ema(result["close"], 50)
    result["ema100"] = _ema(result["close"], 100)
    result["ema200"] = _ema(result["close"], 200)
    result["atr14"] = _atr(result, 14)
    result["adx14"] = _adx(result, 14)
    result["rsi14"] = _rsi(result["close"], 14)
    rng = (result["high"] - result["low"]).replace(0, np.nan)
    result["body_ratio"] = (result["close"] - result["open"]).abs() / rng
    result["atr_pct"] = result["atr14"].rolling(252, min_periods=100).rank(pct=True)
    result["bb_mid"] = result["close"].rolling(20).mean()
    result["bb_std"] = result["close"].rolling(20).std(ddof=0)
    result["bb_width"] = 4 * result["bb_std"] / result["bb_mid"].replace(0, np.nan)
    result["bb_expand"] = result["bb_width"] > result["bb_width"].rolling(20).mean()
    return result


def evaluate_eurusd(
    h4: pd.DataFrame,
    d1: pd.DataFrame,
    params: EURUSDSatelliteParams = EURUSDSatelliteParams(),
) -> Optional[SatelliteSignal]:
    if not params.enabled or len(h4) < 220 or len(d1) < 220:
        return None
    h = add_features(h4).iloc[-1]
    d = add_features(d1).iloc[-1]
    long_bias = (
        params.long_enabled and d["ema50"] > d["ema200"]
        and d["adx14"] >= params.d1_adx_min
        and h["ema21"] > h["ema50"] and h["adx14"] >= params.h4_adx_min
    )
    short_bias = (
        params.short_enabled and d["ema50"] < d["ema200"]
        and d["adx14"] >= params.d1_adx_min
        and h["ema21"] < h["ema50"] and h["adx14"] >= params.h4_adx_min
    )
    if not params.atr_pct_min <= h["atr_pct"] <= params.atr_pct_max:
        return None

    previous = add_features(h4).iloc[-(params.breakout_lookback + 1):-1]
    long_pullback = (
        long_bias and h["low"] <= max(h["ema21"], h["ema50"])
        and h["close"] > h["ema21"] and h["close"] > h["open"]
        and h["body_ratio"] >= params.body_pullback_min
        and 45 <= h["rsi14"] <= 68
    )
    short_pullback = (
        short_bias and h["high"] >= min(h["ema21"], h["ema50"])
        and h["close"] < h["ema21"] and h["close"] < h["open"]
        and h["body_ratio"] >= params.body_pullback_min
        and 32 <= h["rsi14"] <= 55
    )
    long_breakout = (
        long_bias and h["close"] > previous["high"].max()
        and h["body_ratio"] >= params.body_breakout_min and h["rsi14"] < 72
    )
    short_breakout = (
        short_bias and h["close"] < previous["low"].min()
        and h["body_ratio"] >= params.body_breakout_min and h["rsi14"] > 28
    )
    side = Signal.BUY if long_pullback or long_breakout else (
        Signal.SELL if short_pullback or short_breakout else None
    )
    if side is None:
        return None
    is_breakout = long_breakout if side is Signal.BUY else short_breakout
    return SatelliteSignal(
        "EURUSD", side,
        "EURUSD_H4_BREAKOUT" if is_breakout else "EURUSD_H4_PULLBACK",
        pd.Timestamp(h4.iloc[-1]["time"]).to_pydatetime(),
        float(h["atr14"]),
        params.breakout_stop_atr if is_breakout else params.pullback_stop_atr,
        params.target_r,
        params.risk.risk_percent,
        params.risk.partial_fraction,
        params.risk.partial_r,
        params.risk.trail_atr,
        params.risk.max_hold_h4_bars,
        "D1 bias with active H4 pullback or range-break continuation.",
    )


def evaluate_gbpjpy(
    h4: pd.DataFrame,
    d1: pd.DataFrame,
    params: GBPJPYSatelliteParams = GBPJPYSatelliteParams(),
) -> Optional[SatelliteSignal]:
    if not params.enabled or len(h4) < 220 or len(d1) < 220:
        return None
    hf = add_features(h4)
    df = add_features(d1)
    h = hf.iloc[-1]
    d = df.iloc[-1]
    if d["atr_pct"] < params.d1_atr_pct_min:
        return None
    long_bias = (
        params.long_enabled and d["ema50"] > d["ema100"]
        and d["adx14"] >= params.d1_adx_min
        and h["ema21"] > h["ema50"]
    )
    short_bias = (
        params.short_enabled and d["ema50"] < d["ema100"]
        and d["adx14"] >= params.d1_adx_min
        and h["ema21"] < h["ema50"]
    )
    if (h["high"] - h["low"]) > params.oversized_candle_atr * h["atr14"]:
        return None
    previous = hf.iloc[-(params.breakout_lookback + 1):-1]
    expansion = bool(h["bb_expand"]) or h["atr14"] > hf["atr14"].iloc[-6:-1].mean()
    long_breakout = (
        long_bias and expansion and h["close"] > previous["high"].max()
        and h["body_ratio"] >= params.body_breakout_min and h["rsi14"] >= 56
    )
    short_breakout = (
        short_bias and expansion and h["close"] < previous["low"].min()
        and h["body_ratio"] >= params.body_breakout_min and h["rsi14"] <= 44
    )
    long_pullback = (
        long_bias and h["adx14"] >= params.h4_adx_min
        and h["low"] <= h["ema50"] and h["close"] > h["ema21"]
        and h["close"] > h["open"] and h["body_ratio"] >= params.body_pullback_min
    )
    short_pullback = (
        short_bias and h["adx14"] >= params.h4_adx_min
        and h["high"] >= h["ema50"] and h["close"] < h["ema21"]
        and h["close"] < h["open"] and h["body_ratio"] >= params.body_pullback_min
    )
    side = Signal.BUY if long_breakout or long_pullback else (
        Signal.SELL if short_breakout or short_pullback else None
    )
    if side is None:
        return None
    is_breakout = long_breakout if side is Signal.BUY else short_breakout
    return SatelliteSignal(
        "GBPJPY", side,
        "GBPJPY_H4_BREAKOUT" if is_breakout else "GBPJPY_H4_PULLBACK",
        pd.Timestamp(h4.iloc[-1]["time"]).to_pydatetime(),
        float(h["atr14"]),
        params.breakout_stop_atr if is_breakout else params.pullback_stop_atr,
        params.target_r,
        params.risk.risk_percent,
        params.risk.partial_fraction,
        params.risk.partial_r,
        params.risk.trail_atr,
        params.risk.max_hold_h4_bars,
        "D1 momentum bias with H4 volatility breakout or ADX pullback continuation.",
    )
