"""Strategy Engine V7 research signal specifications.

This module generates research signals only. It does not place orders. EURUSD
and GBPJPY stay disabled until forward-demo promotion gates pass. GBPUSD
Satellite V2 is unchanged and imported by the existing application.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

from .enums import Signal


@dataclass(frozen=True)
class V7Signal:
    symbol: str
    side: Signal
    setup: str
    signal_time: datetime
    stop_atr: float
    target_r: float
    break_even_r: float
    max_hold_m15_bars: int
    risk_percent: float
    reason: str


@dataclass(frozen=True)
class V7Config:
    enabled: bool = False
    risk_percent: float = 0.25
    max_eurusd_entries_per_day: int = 1
    max_gbpjpy_entries_per_day: int = 2
    force_flat_hour_utc: int = 20


def _signal(symbol: str, side: Signal, setup: str, row: pd.Series,
            stop_atr: float, target_r: float, max_bars: int,
            risk: float, reason: str) -> V7Signal:
    when = pd.Timestamp(row["end"]).to_pydatetime()
    return V7Signal(symbol, side, setup, when, stop_atr, target_r,
                    1.0, max_bars, risk, reason)


def evaluate_eurusd_v7(row: pd.Series, config: V7Config = V7Config()) -> Optional[V7Signal]:
    """Evaluate completed M15 feature row prepared by the research pipeline."""
    if not config.enabled or row["weekday"] >= 5:
        return None
    hour = float(row["hour"])
    bullish = row["ema20_h1"] > row["ema50_h1"] and row["close_h1"] > row["ema20_h1"] and row["ema20_slope_h1"] > 0
    bearish = row["ema20_h1"] < row["ema50_h1"] and row["close_h1"] < row["ema20_h1"] and row["ema20_slope_h1"] < 0

    compression_long = (
        7 <= hour < 12 and bullish and row["adx14_h1"] <= 18
        and row["asian_range"] <= 1.20 * row["asian_med20"]
        and row["asian_range"] <= row["prev_range"]
        and bool(row["recent_asian_up_break"])
        and row["low"] <= row["asian_high"] + 0.15 * row["atr14"]
        and row["close"] > row["asian_high"] and row["close"] > row["open"]
        and row["body_ratio"] >= 0.30 and row["vol_ratio"] >= 0.85
        and 50 <= row["rsi14"] <= 70
    )
    if compression_long:
        return _signal("EURUSD", Signal.BUY, "EUR_COMPRESSION_LONG", row,
                       1.05, 2.20, 48, config.risk_percent,
                       "Asian compression, upside break and London retest.")

    momentum_short = (
        7 <= hour < 12 and bearish and row["adx14_h1"] >= 20
        and row["close"] < row["prior_16_low"] and row["close"] < row["open"]
        and row["body_ratio"] >= 0.55 and row["vol_ratio"] >= 1.10
        and 28 <= row["rsi14"] <= 48
    )
    if momentum_short:
        return _signal("EURUSD", Signal.SELL, "EUR_MOMENTUM_SHORT", row,
                       1.20, 1.75, 28, config.risk_percent,
                       "Bearish H1 regime and high-volume London momentum break.")

    ny_retest_short = (
        12.5 <= hour < 14.5 and bearish and row["adx14_h1"] >= 18
        and row["prior_day_min_low"] < row["london_low"]
        and row["high"] >= row["london_low"] - 0.15 * row["atr14"]
        and row["close"] < row["london_low"] and row["close"] < row["open"]
        and row["body_ratio"] >= 0.45 and row["vol_ratio"] >= 0.90
        and 30 <= row["rsi14"] <= 46
    )
    if ny_retest_short:
        return _signal("EURUSD", Signal.SELL, "EUR_NY_RETEST_SHORT", row,
                       1.15, 2.0, 40, config.risk_percent,
                       "New York continuation after London-low breakdown and retest.")
    return None


def evaluate_gbpjpy_v7(row: pd.Series, config: V7Config = V7Config()) -> Optional[V7Signal]:
    """Evaluate completed M15 feature row prepared by the research pipeline."""
    if not config.enabled or row["weekday"] >= 5 or not 7 <= float(row["hour"]) < 16:
        return None
    bullish = row["ema20_h1"] > row["ema50_h1"] and row["close_h1"] > row["ema20_h1"] and row["ema20_slope_h1"] > 0
    bearish = row["ema20_h1"] < row["ema50_h1"] and row["close_h1"] < row["ema20_h1"] and row["ema20_slope_h1"] < 0

    momentum_long = (
        bullish and row["adx14_h1"] >= 18 and row["atr14_h1"] >= row["atr_q60_h1"]
        and row["close"] > row["prior_16_high"] and row["close"] > row["open"]
        and row["body_ratio"] >= 0.45 and row["vol_ratio"] >= 1.10
        and 54 <= row["rsi14"] <= 74 and row["range_atr"] <= 2.0
    )
    if momentum_long:
        return _signal("GBPJPY", Signal.BUY, "GJ_MOMENTUM_LONG", row,
                       1.50, 2.0, 32, config.risk_percent,
                       "Bullish H1 volatility regime and 16-bar momentum break.")

    pullback_short = (
        bearish and row["ema20_m30"] < row["ema50_m30"]
        and row["adx14_h1"] >= 18 and row["atr14_h1"] >= row["atr_q55_h1"]
        and bool(row["recent_m30_pullback_touch"])
        and row["close"] < row["prior_2_low"] and row["close"] < row["open"]
        and row["body_ratio"] >= 0.55 and row["vol_ratio"] >= 1.10
        and 30 <= row["rsi14"] <= 48
    )
    if pullback_short:
        return _signal("GBPJPY", Signal.SELL, "GJ_PULLBACK_SHORT", row,
                       1.60, 2.0, 40, config.risk_percent,
                       "Bearish H1/M30 pullback followed by M15 momentum resumption.")
    return None
