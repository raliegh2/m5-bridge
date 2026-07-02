"""Evidence-gated Satellite V5 models.

GBPUSD V4 Swing and GBPUSD Satellite V2 are unchanged.

EURUSD V5 wraps the V4 pullback-resumption signal with a 10:00-11:00 UTC
entry window and a smoother-H1-candle gate. The Asian false-break family is
implemented but disabled by default because its direct backtest was negative.

GBPJPY V5 wraps the profitable V4 breakout-retest signal with an 08:00-10:00
UTC gate. The 55%-65% ATR tier and ADX deep-pullback family are implemented but
disabled by default because they reduced expectancy in the supplied sample.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import pandas as pd

from .enums import Signal
from .gbpusd_v4 import _adx, _atr, _ema
from .satellites_v4 import (
    EURUSDV4Params,
    GBPJPYV4Params,
    SatelliteV4Signal,
    evaluate_eurusd_v4,
    evaluate_gbpjpy_v4,
)


@dataclass(frozen=True)
class EURUSDV5Params:
    enabled: bool = False
    risk_percent: float = 0.10
    max_h1_candle_atr: float = 1.35
    require_resistance_space: bool = False
    minimum_resistance_r: float = 1.50
    enable_asian_false_break: bool = False


@dataclass(frozen=True)
class GBPJPYV5Params:
    enabled: bool = False
    risk_percent: float = 0.10
    enable_low_atr_tier: bool = False
    enable_deep_pullback: bool = False
    low_atr_min: float = 0.55
    low_atr_max: float = 0.65


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = average_gain / average_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["atr14"] = _atr(result, 14)
    result["adx14"] = _adx(result, 14)
    result["ema20"] = _ema(result["close"], 20)
    result["ema50"] = _ema(result["close"], 50)
    result["ema20_slope"] = result["ema20"].diff(3)
    result["rsi14"] = _rsi(result["close"], 14)
    result["body_ratio"] = (
        (result["close"] - result["open"]).abs()
        / (result["high"] - result["low"]).replace(0, np.nan)
    )
    result["volume_ratio"] = (
        result["tick_volume"]
        / result["tick_volume"].rolling(20, min_periods=20).mean()
    )
    result["atr_percentile"] = result["atr14"].rolling(
        500, min_periods=200
    ).rank(pct=True)
    return result


def _previous_day_high(m15: pd.DataFrame, signal_time: pd.Timestamp) -> float:
    day = signal_time.floor("D")
    previous = m15[(m15["time"] >= day - pd.Timedelta(days=1)) & (m15["time"] < day)]
    return float(previous["high"].max()) if not previous.empty else float("nan")


def _resistance_space_ok(
    m15: pd.DataFrame,
    signal: SatelliteV4Signal,
    minimum_r: float,
) -> bool:
    previous_high = _previous_day_high(m15, pd.Timestamp(signal.signal_time))
    if not np.isfinite(previous_high) or previous_high <= float(m15.iloc[-1]["close"]):
        return True
    stop_distance = signal.stop_atr * signal.atr_price
    return previous_high - float(m15.iloc[-1]["close"]) >= minimum_r * stop_distance


def _evaluate_asian_false_break(
    m15: pd.DataFrame,
    h1: pd.DataFrame,
    params: EURUSDV5Params,
) -> Optional[SatelliteV4Signal]:
    if not params.enable_asian_false_break or len(m15) < 220 or len(h1) < 220:
        return None
    now = pd.Timestamp(m15.iloc[-1]["time"]) + pd.Timedelta(minutes=15)
    hour = now.hour + now.minute / 60
    if now.weekday() >= 5 or not 10 <= hour < 11:
        return None
    hf = _feature_frame(h1)
    mf = _feature_frame(m15)
    anchor = hf.iloc[-1]
    row = mf.iloc[-1]
    if not (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= 20
        and anchor["ema20_slope"] > 0
    ):
        return None
    day = now.floor("D")
    asian = mf[(mf["time"] >= day) & (mf["time"] < day + pd.Timedelta(hours=7))]
    prior = mf[(mf["time"] >= day) & (mf["time"] < m15.iloc[-1]["time"])]
    if asian.empty or prior.empty:
        return None
    asian_low = float(asian["low"].min())
    asian_high = float(asian["high"].max())
    swept = float(prior["low"].min()) < asian_low - 0.05 * float(row["atr14"])
    trigger = (
        swept
        and row["low"] <= asian_low + 0.10 * row["atr14"]
        and row["close"] > asian_low
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.45
        and row["volume_ratio"] >= 1.05
        and 46 <= row["rsi14"] <= 62
        and asian_high - row["close"] >= 1.5 * row["atr14"]
    )
    if not trigger:
        return None
    return SatelliteV4Signal(
        symbol="EURUSD",
        side=Signal.BUY,
        setup="EURUSD_ASIAN_FALSE_BREAK_V5",
        signal_time=now.to_pydatetime(),
        atr_price=float(row["atr14"]),
        stop_atr=1.0,
        target_r=2.0,
        risk_percent=params.risk_percent,
        partial_fraction=0.50,
        partial_r=1.0,
        trail_atr=1.50,
        max_hold_m15_bars=32,
        reason="Bullish H1 regime with an Asian-low sweep and late-London reclaim.",
    )


def evaluate_eurusd_v5(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: EURUSDV5Params = EURUSDV5Params(),
) -> Optional[SatelliteV4Signal]:
    if not params.enabled:
        return None
    base = evaluate_eurusd_v4(
        m15,
        m30,
        h1,
        replace(EURUSDV4Params(), enabled=True, risk_percent=params.risk_percent),
    )
    if base is not None:
        signal_time = pd.Timestamp(base.signal_time)
        hour = signal_time.hour + signal_time.minute / 60
        hf = _feature_frame(h1)
        anchor = hf.iloc[-1]
        h1_range_atr = (
            float(anchor["high"] - anchor["low"]) / float(anchor["atr14"])
        )
        if 10 <= hour < 11 and h1_range_atr <= params.max_h1_candle_atr:
            if not params.require_resistance_space or _resistance_space_ok(
                m15, base, params.minimum_resistance_r
            ):
                return replace(base, risk_percent=params.risk_percent, setup="EURUSD_PULLBACK_V5")
    return _evaluate_asian_false_break(m15, h1, params)


def _evaluate_gbpjpy_low_atr_tier(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: GBPJPYV5Params,
) -> Optional[SatelliteV4Signal]:
    if not params.enable_low_atr_tier or min(len(m15), len(m30), len(h1)) < 220:
        return None
    mf = _feature_frame(m15)
    m30f = _feature_frame(m30)
    hf = _feature_frame(h1)
    row, context, anchor = mf.iloc[-1], m30f.iloc[-1], hf.iloc[-1]
    now = pd.Timestamp(m15.iloc[-1]["time"]) + pd.Timedelta(minutes=15)
    hour = now.hour + now.minute / 60
    if now.weekday() >= 5 or not 9 <= hour < 10:
        return None
    if not (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= 22
        and anchor["ema20_slope"] > 0
        and params.low_atr_min <= anchor["atr_percentile"] < params.low_atr_max
    ):
        return None
    level = float(m30f["high"].shift(1).rolling(8).max().iloc[-1])
    recent_break = bool((m30f["close"].iloc[-4:] > m30f["high"].shift(1).rolling(8).max().iloc[-4:]).any())
    trigger = (
        recent_break
        and row["low"] <= level + 0.20 * row["atr14"]
        and row["close"] > level
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.50
        and row["volume_ratio"] >= 1.15
        and context["volume_ratio"] >= 1.34
        and 55 <= row["rsi14"] <= 70
    )
    if not trigger:
        return None
    return SatelliteV4Signal(
        symbol="GBPJPY",
        side=Signal.BUY,
        setup="GBPJPY_LOW_ATR_BREAKOUT_RETEST_V5",
        signal_time=now.to_pydatetime(),
        atr_price=float(row["atr14"]),
        stop_atr=1.40,
        target_r=2.80,
        risk_percent=params.risk_percent,
        partial_fraction=0.40,
        partial_r=1.20,
        trail_atr=2.0,
        max_hold_m15_bars=64,
        reason="Tiered 55%-65% ATR regime with stricter 09:00 breakout-retest quality.",
    )


def _evaluate_gbpjpy_deep_pullback(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: GBPJPYV5Params,
) -> Optional[SatelliteV4Signal]:
    if not params.enable_deep_pullback or min(len(m15), len(m30), len(h1)) < 220:
        return None
    mf, m30f, hf = _feature_frame(m15), _feature_frame(m30), _feature_frame(h1)
    row, context, anchor = mf.iloc[-1], m30f.iloc[-1], hf.iloc[-1]
    now = pd.Timestamp(m15.iloc[-1]["time"]) + pd.Timedelta(minutes=15)
    hour = now.hour + now.minute / 60
    if now.weekday() >= 5 or not 8 <= hour < 10:
        return None
    higher_low = row["low"] > mf["low"].shift(2).rolling(6).min().iloc[-1]
    trigger = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= 28
        and anchor["atr_percentile"] >= 0.55
        and context["low"] <= context["ema20"] + 0.15 * context["atr14"]
        and context["close"] >= context["ema50"]
        and higher_low
        and row["close"] > row["open"]
        and row["close"] > mf["high"].shift(1).rolling(2).max().iloc[-1]
        and row["body_ratio"] >= 0.45
        and row["volume_ratio"] >= 0.95
        and 52 <= row["rsi14"] <= 68
    )
    if not trigger:
        return None
    return SatelliteV4Signal(
        symbol="GBPJPY",
        side=Signal.BUY,
        setup="GBPJPY_ADX_DEEP_PULLBACK_V5",
        signal_time=now.to_pydatetime(),
        atr_price=float(row["atr14"]),
        stop_atr=1.60,
        target_r=2.50,
        risk_percent=params.risk_percent,
        partial_fraction=0.40,
        partial_r=1.20,
        trail_atr=2.0,
        max_hold_m15_bars=64,
        reason="Strong H1 ADX regime with M30 pullback and M15 higher-low resumption.",
    )


def evaluate_gbpjpy_v5(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: GBPJPYV5Params = GBPJPYV5Params(),
) -> Optional[SatelliteV4Signal]:
    if not params.enabled:
        return None
    base = evaluate_gbpjpy_v4(
        m15,
        m30,
        h1,
        replace(GBPJPYV4Params(), enabled=True, risk_percent=params.risk_percent),
    )
    if base is not None:
        signal_time = pd.Timestamp(base.signal_time)
        hour = signal_time.hour + signal_time.minute / 60
        if 8 <= hour < 10:
            return replace(base, risk_percent=params.risk_percent, setup="GBPJPY_BREAKOUT_RETEST_V5")
    low_atr = _evaluate_gbpjpy_low_atr_tier(m15, m30, h1, params)
    if low_atr is not None:
        return low_atr
    return _evaluate_gbpjpy_deep_pullback(m15, m30, h1, params)
