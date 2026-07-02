"""GBPUSD Swing V5 research wrapper.

The frozen GBPUSD V4 breakout engine is unchanged. V5 adds one lower-risk H4
trend-pullback family to increase opportunity modestly without turning the swing
engine into an intraday satellite. This module generates research signals only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from .enums import Signal


@dataclass(frozen=True)
class GBPUSDSwingV5Signal:
    side: Signal
    setup: str
    signal_time: datetime
    stop_atr: float
    target_r: float
    partial_fraction: float
    partial_r: float
    trail_atr: float
    max_hold_h4_bars: int
    risk_percent: float
    reason: str


@dataclass(frozen=True)
class GBPUSDSwingV5Params:
    enabled: bool = False
    addon_risk_percent: float = 0.20
    allowed_bar_end_hours_utc: tuple[int, ...] = (8, 12, 16)
    adx_min: float = 20.0
    pullback_tolerance_atr: float = 0.30
    body_ratio_min: float = 0.55
    volume_ratio_min: float = 1.0
    atr_ratio_min: float = 1.0


def evaluate_gbpusd_swing_v5_addon(
    row: pd.Series,
    params: GBPUSDSwingV5Params = GBPUSDSwingV5Params(),
) -> Optional[GBPUSDSwingV5Signal]:
    """Evaluate one completed H4 feature row for the V5 pullback add-on."""
    if not params.enabled or int(row["hour"]) not in params.allowed_bar_end_hours_utc:
        return None
    if int(row["weekday"]) >= 5:
        return None

    long_bias = (
        row["close_d1"] > row["ema20_d1"] > row["ema50_d1"]
        and row["ema20_h4"] > row["ema50_h4"]
        and row["ema20_slope_h4"] > 0
    )
    short_bias = (
        row["close_d1"] < row["ema20_d1"] < row["ema50_d1"]
        and row["ema20_h4"] < row["ema50_h4"]
        and row["ema20_slope_h4"] < 0
    )
    quality = (
        row["adx14"] >= params.adx_min
        and row["body_ratio"] >= params.body_ratio_min
        and row["volume_ratio"] >= params.volume_ratio_min
        and row["atr_ratio"] >= params.atr_ratio_min
    )

    long_trigger = (
        long_bias and quality
        and row["prior3_low"] <= row["ema20_h4"] + params.pullback_tolerance_atr * row["atr14"]
        and row["close"] > row["open"]
        and row["close_location"] >= 0.60
    )
    short_trigger = (
        short_bias and quality
        and row["prior3_high"] >= row["ema20_h4"] - params.pullback_tolerance_atr * row["atr14"]
        and row["close"] < row["open"]
        and row["close_location"] <= 0.40
    )
    if not long_trigger and not short_trigger:
        return None

    return GBPUSDSwingV5Signal(
        side=Signal.BUY if long_trigger else Signal.SELL,
        setup="GBPUSD_SWING_V5_PULLBACK_ADDON",
        signal_time=pd.Timestamp(row["bar_end"]).to_pydatetime(),
        stop_atr=1.25,
        target_r=2.50,
        partial_fraction=0.50,
        partial_r=1.0,
        trail_atr=2.0,
        max_hold_h4_bars=36,
        risk_percent=params.addon_risk_percent,
        reason="D1/H4 aligned trend, three-bar EMA20 pullback and strong H4 resumption.",
    )
