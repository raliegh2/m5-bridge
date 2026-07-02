"""Satellite V6 research models.

GBPUSD V4 Swing and GBPUSD Satellite V2 are unchanged.

V6 keeps the evidence-gated V5 core setups and adds independent, separately
switchable setup families. Every optional family is disabled when the supplied
history did not show a stable development/validation edge.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import pandas as pd

from .enums import Signal
from .gbpusd_v4 import _adx, _atr, _ema
from .satellites_v4 import SatelliteV4Signal
from .satellites_v5 import (
    EURUSDV5Params,
    GBPJPYV5Params,
    evaluate_eurusd_v5,
    evaluate_gbpjpy_v5,
)


@dataclass(frozen=True)
class EURUSDV6Params:
    enabled: bool = False
    retain_1000_quality_setup: bool = True
    enable_london_pullback: bool = False
    enable_compression_breakout: bool = True
    enable_defensive_ny_continuation: bool = True
    quality_risk_percent: float = 0.10
    london_pullback_risk_percent: float = 0.05
    compression_risk_percent: float = 0.075
    ny_risk_percent: float = 0.075


@dataclass(frozen=True)
class GBPJPYV6Params:
    enabled: bool = False
    retain_premium_breakout_retest: bool = True
    enable_expansion_breakout: bool = True
    enable_compression_expansion: bool = False
    enable_second_leg: bool = False
    enable_atr_60_65_tier: bool = False
    premium_risk_percent: float = 0.10
    expansion_risk_percent: float = 0.10
    optional_risk_percent: float = 0.05


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["atr14"] = _atr(result, 14)
    result["adx14"] = _adx(result, 14)
    result["rsi14"] = _rsi(result["close"], 14)
    result["ema20"] = _ema(result["close"], 20)
    result["ema50"] = _ema(result["close"], 50)
    result["ema20_slope"] = result["ema20"].diff(3)
    candle_range = (result["high"] - result["low"]).replace(0, np.nan)
    result["body_ratio"] = (result["close"] - result["open"]).abs() / candle_range
    result["range_atr"] = candle_range / result["atr14"]
    result["volume_ratio"] = (
        result["tick_volume"]
        / result["tick_volume"].rolling(20, min_periods=20).mean()
    )
    result["atr_q55"] = result["atr14"].rolling(500, min_periods=200).quantile(0.55)
    result["atr_q60"] = result["atr14"].rolling(500, min_periods=200).quantile(0.60)
    result["atr_q65"] = result["atr14"].rolling(500, min_periods=200).quantile(0.65)
    return result


def _session_range(
    frame: pd.DataFrame,
    when: pd.Timestamp,
    start_hour: float,
    end_hour: float,
) -> tuple[float, float]:
    day = when.floor("D")
    hours = frame["time"].dt.hour + frame["time"].dt.minute / 60
    selected = frame[
        (frame["time"] >= day)
        & (frame["time"] < day + pd.Timedelta(days=1))
        & (hours >= start_hour)
        & (hours < end_hour)
    ]
    if selected.empty:
        return float("nan"), float("nan")
    return float(selected["high"].max()), float(selected["low"].min())


def _previous_day(frame: pd.DataFrame, when: pd.Timestamp) -> pd.DataFrame:
    day = when.floor("D")
    return frame[
        (frame["time"] >= day - pd.Timedelta(days=1))
        & (frame["time"] < day)
    ]


def _signal(
    symbol: str,
    side: Signal,
    setup: str,
    when: pd.Timestamp,
    atr_price: float,
    stop_atr: float,
    target_r: float,
    risk_percent: float,
    partial_fraction: float,
    partial_r: float,
    trail_atr: float,
    max_hold_m15_bars: int,
    reason: str,
) -> SatelliteV4Signal:
    return SatelliteV4Signal(
        symbol=symbol,
        side=side,
        setup=setup,
        signal_time=when.to_pydatetime(),
        atr_price=atr_price,
        stop_atr=stop_atr,
        target_r=target_r,
        risk_percent=risk_percent,
        partial_fraction=partial_fraction,
        partial_r=partial_r,
        trail_atr=trail_atr,
        max_hold_m15_bars=max_hold_m15_bars,
        reason=reason,
    )


def _eurusd_london_pullback(
    m15f: pd.DataFrame,
    m30f: pd.DataFrame,
    h1f: pd.DataFrame,
    risk_percent: float,
) -> Optional[SatelliteV4Signal]:
    row, context, anchor = m15f.iloc[-1], m30f.iloc[-1], h1f.iloc[-1]
    when = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = when.hour + when.minute / 60
    if not 7.5 <= hour < 10.5:
        return None
    previous = _previous_day(m15f, when)
    if previous.empty:
        return None
    previous_mid = (float(previous["high"].max()) + float(previous["low"].min())) / 2
    pullback = m15f.iloc[-5:-1]
    trigger = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and 18 <= anchor["adx14"] <= 35
        and anchor["ema20_slope"] > 0
        and context["ema20"] > context["ema50"]
        and context["close"] > context["ema20"]
        and row["close"] > previous_mid
        and int((pullback["close"] < pullback["open"]).sum()) in {1, 2, 3}
        and float(pullback["low"].min()) <= row["ema20"] + 0.12 * row["atr14"]
        and float(pullback["low"].min()) >= context["ema50"] - 0.10 * context["atr14"]
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.42
        and row["volume_ratio"] >= 0.95
        and 50 <= row["rsi14"] <= 66
        and row["close"] > float(m15f["high"].iloc[-3:-1].max())
    )
    if not trigger:
        return None
    return _signal(
        "EURUSD", Signal.BUY, "EURUSD_LONDON_PULLBACK_V6", when,
        float(row["atr14"]), 1.10, 2.0, risk_percent, 0.50, 1.0, 1.50, 40,
        "Independent London structural pullback. Disabled by default after weak validation.",
    )


def _eurusd_compression_breakout(
    m15f: pd.DataFrame,
    h1f: pd.DataFrame,
    risk_percent: float,
) -> Optional[SatelliteV4Signal]:
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    when = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = when.hour + when.minute / 60
    if not 7 <= hour < 11:
        return None
    asian_high, asian_low = _session_range(m15f, when, 0, 7)
    previous = _previous_day(m15f, when)
    if not np.isfinite(asian_high) or previous.empty:
        return None
    previous_range = float(previous["high"].max() - previous["low"].min())
    daily = m15f.copy()
    daily["date"] = daily["time"].dt.floor("D")
    hours = daily["time"].dt.hour + daily["time"].dt.minute / 60
    asian_history = daily[hours < 7].groupby("date").agg(
        high=("high", "max"), low=("low", "min")
    )
    asian_median = float((asian_history["high"] - asian_history["low"]).tail(20).median())
    recent = m15f.iloc[-7:-1]
    prior_break = bool(
        (
            (recent["close"] > asian_high)
            & (recent["body_ratio"] >= 0.50)
            & (recent["volume_ratio"] >= 1.20)
        ).any()
    )
    trigger = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["ema20_slope"] > 0
        and anchor["adx14"] <= 22
        and (asian_high - asian_low) <= 1.05 * asian_median
        and (asian_high - asian_low) <= 0.90 * previous_range
        and prior_break
        and row["low"] <= asian_high + 0.15 * row["atr14"]
        and row["close"] > asian_high
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.30
        and row["volume_ratio"] >= 0.85
        and 50 <= row["rsi14"] <= 70
    )
    if not trigger:
        return None
    return _signal(
        "EURUSD", Signal.BUY, "EURUSD_COMPRESSION_BREAKOUT_V6", when,
        float(row["atr14"]), 1.05, 2.20, risk_percent, 0.50, 1.0, 1.50, 48,
        "Compressed Asian range, high-volume upside break and first London retest.",
    )


def _eurusd_defensive_ny(
    m15f: pd.DataFrame,
    h1f: pd.DataFrame,
    risk_percent: float,
) -> Optional[SatelliteV4Signal]:
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    when = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = when.hour + when.minute / 60
    if not 12.5 <= hour < 14.5:
        return None
    london_high, london_low = _session_range(m15f, when, 7, 12)
    prior_day = m15f[
        (m15f["time"] >= when.floor("D"))
        & (m15f["time"] < row["time"])
    ]
    if not np.isfinite(london_low) or prior_day.empty:
        return None
    trigger = (
        anchor["ema20"] < anchor["ema50"]
        and anchor["close"] < anchor["ema20"]
        and anchor["ema20_slope"] < 0
        and anchor["adx14"] >= 18
        and float(prior_day["low"].min()) < london_low
        and row["high"] >= london_low - 0.15 * row["atr14"]
        and row["close"] < london_low
        and row["close"] < row["open"]
        and row["body_ratio"] >= 0.45
        and row["volume_ratio"] >= 0.90
        and 30 <= row["rsi14"] <= 46
    )
    if not trigger:
        return None
    return _signal(
        "EURUSD", Signal.SELL, "EURUSD_DEFENSIVE_NY_SHORT_V6", when,
        float(row["atr14"]), 1.15, 2.0, risk_percent, 0.50, 1.0, 1.50, 40,
        "Defensive New York continuation after a bearish London-range break and retest.",
    )


def evaluate_eurusd_v6(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: EURUSDV6Params = EURUSDV6Params(),
) -> Optional[SatelliteV4Signal]:
    if not params.enabled or min(len(m15), len(m30), len(h1)) < 520:
        return None
    if params.retain_1000_quality_setup:
        retained = evaluate_eurusd_v5(
            m15, m30, h1,
            replace(EURUSDV5Params(), enabled=True, risk_percent=params.quality_risk_percent),
        )
        if retained is not None:
            return replace(retained, setup="EURUSD_1000_QUALITY_V6")
    m15f, m30f, h1f = _features(m15), _features(m30), _features(h1)
    if params.enable_compression_breakout:
        signal = _eurusd_compression_breakout(m15f, h1f, params.compression_risk_percent)
        if signal is not None:
            return signal
    if params.enable_defensive_ny_continuation:
        signal = _eurusd_defensive_ny(m15f, h1f, params.ny_risk_percent)
        if signal is not None:
            return signal
    if params.enable_london_pullback:
        return _eurusd_london_pullback(m15f, m30f, h1f, params.london_pullback_risk_percent)
    return None


def _gbpjpy_expansion_breakout(
    m15f: pd.DataFrame,
    h1f: pd.DataFrame,
    risk_percent: float,
) -> Optional[SatelliteV4Signal]:
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    when = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = when.hour + when.minute / 60
    if not 7 <= hour < 12:
        return None
    lookback = m15f.iloc[-45:-13]
    recent = m15f.iloc[-13:-1]
    if len(lookback) < 32:
        return None
    level = float(lookback["high"].max())
    breakout = recent[
        (recent["close"] > level)
        & (recent["body_ratio"] >= 0.35)
        & (recent["volume_ratio"] >= 1.20)
    ]
    q60 = float(h1f["atr14"].tail(500).quantile(0.60))
    trigger = (
        not breakout.empty
        and anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["ema20_slope"] > 0
        and anchor["adx14"] >= 22
        and anchor["atr14"] >= q60
        and anchor["range_atr"] <= 2.0
        and row["low"] <= level + 0.20 * row["atr14"]
        and row["close"] > level
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.30
        and row["volume_ratio"] >= 0.90
        and 54 <= row["rsi14"] <= 72
    )
    if not trigger:
        return None
    return _signal(
        "GBPJPY", Signal.BUY, "GBPJPY_EXPANSION_BREAKOUT_V6", when,
        float(row["atr14"]), 1.40, 2.80, risk_percent, 0.40, 1.20, 2.0, 64,
        "High-volume 32-bar expansion break followed by a London M15 retest.",
    )


def _gbpjpy_compression_expansion(
    m15f: pd.DataFrame,
    h1f: pd.DataFrame,
    risk_percent: float,
) -> Optional[SatelliteV4Signal]:
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    when = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = when.hour + when.minute / 60
    if not 7 <= hour < 11:
        return None
    prior = m15f.iloc[-25:-1]
    short_range = float(prior["range_atr"].tail(6).mean())
    long_range = float(prior["range_atr"].mean())
    level = float(prior["high"].max())
    trigger = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= 22
        and short_range < 0.90 * long_range
        and row["close"] > level
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.55
        and row["volume_ratio"] >= 1.10
        and 54 <= row["rsi14"] <= 72
    )
    if not trigger:
        return None
    return _signal(
        "GBPJPY", Signal.BUY, "GBPJPY_COMPRESSION_EXPANSION_V6", when,
        float(row["atr14"]), 1.45, 2.60, risk_percent, 0.40, 1.20, 2.0, 64,
        "Compression-to-expansion family. Disabled by default after weak research results.",
    )


def _gbpjpy_second_leg(
    m15f: pd.DataFrame,
    h1f: pd.DataFrame,
    risk_percent: float,
) -> Optional[SatelliteV4Signal]:
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    when = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = when.hour + when.minute / 60
    if not 8 <= hour < 11:
        return None
    prior = m15f.iloc[-25:-1]
    level = float(prior["high"].iloc[:12].max())
    impulse_high = float(prior["high"].max())
    impulse = impulse_high - level
    retracement = (impulse_high - float(row["low"])) / impulse if impulse > 0 else np.nan
    trigger = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= 24
        and impulse >= 0.80 * anchor["atr14"]
        and 0.30 <= retracement <= 0.60
        and row["close"] > row["open"]
        and row["close"] > float(m15f["high"].iloc[-3:-1].max())
        and row["body_ratio"] >= 0.40
        and row["volume_ratio"] >= 0.95
        and 53 <= row["rsi14"] <= 69
    )
    if not trigger:
        return None
    return _signal(
        "GBPJPY", Signal.BUY, "GBPJPY_SECOND_LEG_V6", when,
        float(row["atr14"]), 1.55, 2.50, risk_percent, 0.40, 1.20, 2.0, 72,
        "Breakout second-leg continuation. Disabled until an independent edge appears.",
    )


def _gbpjpy_atr_tier(
    m15f: pd.DataFrame,
    h1f: pd.DataFrame,
    risk_percent: float,
) -> Optional[SatelliteV4Signal]:
    row, anchor = m15f.iloc[-1], h1f.iloc[-1]
    when = pd.Timestamp(row["time"]) + pd.Timedelta(minutes=15)
    hour = when.hour + when.minute / 60
    if not 9 <= hour < 10:
        return None
    history = h1f["atr14"].tail(500)
    q60, q65 = float(history.quantile(0.60)), float(history.quantile(0.65))
    prior = m15f.iloc[-33:-1]
    level = float(prior["high"].max())
    trigger = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= 28
        and q60 <= anchor["atr14"] < q65
        and row["close"] > level
        and row["close"] > row["open"]
        and row["body_ratio"] >= 0.55
        and row["volume_ratio"] >= 1.10
        and 55 <= row["rsi14"] <= 70
    )
    if not trigger:
        return None
    return _signal(
        "GBPJPY", Signal.BUY, "GBPJPY_ATR_60_65_TIER_V6", when,
        float(row["atr14"]), 1.40, 2.80, risk_percent, 0.40, 1.20, 2.0, 64,
        "Restricted 60%-65% ATR tier. Disabled by default after no robust incremental edge.",
    )


def evaluate_gbpjpy_v6(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: GBPJPYV6Params = GBPJPYV6Params(),
) -> Optional[SatelliteV4Signal]:
    if not params.enabled or min(len(m15), len(m30), len(h1)) < 520:
        return None
    if params.retain_premium_breakout_retest:
        retained = evaluate_gbpjpy_v5(
            m15, m30, h1,
            replace(GBPJPYV5Params(), enabled=True, risk_percent=params.premium_risk_percent),
        )
        if retained is not None:
            return replace(retained, setup="GBPJPY_PREMIUM_BREAKOUT_RETEST_V6")
    m15f, h1f = _features(m15), _features(h1)
    if params.enable_expansion_breakout:
        signal = _gbpjpy_expansion_breakout(m15f, h1f, params.expansion_risk_percent)
        if signal is not None:
            return signal
    if params.enable_compression_expansion:
        signal = _gbpjpy_compression_expansion(m15f, h1f, params.optional_risk_percent)
        if signal is not None:
            return signal
    if params.enable_second_leg:
        signal = _gbpjpy_second_leg(m15f, h1f, params.optional_risk_percent)
        if signal is not None:
            return signal
    if params.enable_atr_60_65_tier:
        return _gbpjpy_atr_tier(m15f, h1f, params.optional_risk_percent)
    return None
