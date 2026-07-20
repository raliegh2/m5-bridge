"""V14.22 research-only range-breakout/retest arm.

The failed V14.19 mean-reversion orders remain permanently disabled. This
module tests a different economic hypothesis: a completed D1 range is used as
compression, then a later H4 displacement through the frozen range boundary
must retest and hold before a shadow trade is admitted.

No function in this module imports MetaTrader, sends an order, or allocates
risk. Promotion remains impossible unless chronological retail/stress and
forward gates pass in the separate V14.22 validation replay.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from .v14_19_range_mean_reversion_shadow import (
    CORE_SYMBOLS,
    adx,
    atr,
    ema,
    resample_ohlc,
    resolve_bar_exit,
    signal_available_at,
)


@dataclass(frozen=True)
class RangeBreakoutRetestProfile:
    name: str
    adx_maximum: float = 25.0
    maximum_ema_gap_atr: float = 0.80
    minimum_range_width_atr: float = 2.50
    maximum_range_width_atr: float = 12.0
    maximum_atr_to_median: float = 1.10
    breakout_buffer_atr: float = 0.05
    minimum_breakout_body_h4_atr: float = 0.65
    minimum_breakout_close_location: float = 0.70
    maximum_breakout_wait_h4_bars: int = 30
    maximum_retest_wait_h4_bars: int = 3
    retest_tolerance_atr: float = 0.15
    stop_buffer_atr: float = 0.10
    minimum_stop_atr: float = 0.35
    maximum_stop_atr: float = 1.25
    target_r: float = 2.00
    maximum_holding_h4_bars: int = 18
    slippage_reserve_r: float = 0.025


PROFILES = (
    RangeBreakoutRetestProfile(name="BALANCED_2R"),
    RangeBreakoutRetestProfile(
        name="CONSERVATIVE_2_5R",
        breakout_buffer_atr=0.10,
        minimum_breakout_body_h4_atr=0.85,
        minimum_breakout_close_location=0.75,
        maximum_retest_wait_h4_bars=4,
        retest_tolerance_atr=0.10,
        stop_buffer_atr=0.15,
        minimum_stop_atr=0.40,
        maximum_stop_atr=1.35,
        target_r=2.50,
        maximum_holding_h4_bars=24,
    ),
    RangeBreakoutRetestProfile(
        name="FAST_1_5R",
        maximum_atr_to_median=1.00,
        minimum_breakout_body_h4_atr=0.50,
        minimum_breakout_close_location=0.65,
        maximum_breakout_wait_h4_bars=24,
        maximum_retest_wait_h4_bars=2,
        retest_tolerance_atr=0.20,
        stop_buffer_atr=0.08,
        minimum_stop_atr=0.30,
        maximum_stop_atr=1.10,
        target_r=1.50,
        maximum_holding_h4_bars=15,
    ),
)


def _daily_range_frame(h1_bid: pd.DataFrame, profile: RangeBreakoutRetestProfile) -> pd.DataFrame:
    daily = resample_ohlc(h1_bid, "1D").sort_values("time").reset_index(drop=True)
    daily["atr14"] = atr(daily)
    daily["adx14"] = adx(daily)
    daily["ema20"] = ema(daily["close"], 20)
    daily["ema50"] = ema(daily["close"], 50)
    daily["prior_high20"] = daily["high"].rolling(20, min_periods=20).max().shift(1)
    daily["prior_low20"] = daily["low"].rolling(20, min_periods=20).min().shift(1)
    daily["ema_gap_atr"] = (
        (daily["ema20"] - daily["ema50"]).abs()
        / daily["atr14"].replace(0, np.nan)
    )
    daily["range_width_atr"] = (
        (daily["prior_high20"] - daily["prior_low20"])
        / daily["atr14"].replace(0, np.nan)
    )
    atr_median = daily["atr14"].rolling(60, min_periods=30).median()
    daily["atr_to_median"] = daily["atr14"] / atr_median.replace(0, np.nan)
    daily["available_at"] = daily["time"].map(signal_available_at)
    daily["range_state"] = (
        (daily["adx14"] <= profile.adx_maximum)
        & (daily["ema_gap_atr"] <= profile.maximum_ema_gap_atr)
        & (daily["range_width_atr"] >= profile.minimum_range_width_atr)
        & (daily["range_width_atr"] <= profile.maximum_range_width_atr)
        & (daily["atr_to_median"] <= profile.maximum_atr_to_median)
    )
    return daily


def _execution_frame(h1_bid: pd.DataFrame, h1_ask: pd.DataFrame) -> pd.DataFrame:
    bid = resample_ohlc(h1_bid, "4h").rename(
        columns={
            "open": "bid_open",
            "high": "bid_high",
            "low": "bid_low",
            "close": "bid_close",
            "tick_volume": "bid_tick_volume",
        }
    )
    ask = resample_ohlc(h1_ask, "4h").rename(
        columns={
            "open": "ask_open",
            "high": "ask_high",
            "low": "ask_low",
            "close": "ask_close",
            "tick_volume": "ask_tick_volume",
        }
    )
    frame = (
        bid.merge(ask, on="time", how="inner")
        .sort_values("time")
        .drop_duplicates("time")
        .reset_index(drop=True)
    )
    midpoint = pd.DataFrame(
        {
            "high": (frame["bid_high"] + frame["ask_high"]) / 2.0,
            "low": (frame["bid_low"] + frame["ask_low"]) / 2.0,
            "close": (frame["bid_close"] + frame["ask_close"]) / 2.0,
        }
    )
    frame["h4_atr14"] = atr(midpoint)
    frame["mid_open"] = (frame["bid_open"] + frame["ask_open"]) / 2.0
    frame["mid_high"] = midpoint["high"]
    frame["mid_low"] = midpoint["low"]
    frame["mid_close"] = midpoint["close"]
    frame["body_h4_atr"] = (
        (frame["mid_close"] - frame["mid_open"]).abs()
        / frame["h4_atr14"].replace(0, np.nan)
    )
    candle_range = (frame["mid_high"] - frame["mid_low"]).replace(0, np.nan)
    frame["close_location"] = (
        (frame["mid_close"] - frame["mid_low"]) / candle_range
    )
    return frame


def _find_breakout(
    frame: pd.DataFrame,
    start_index: int,
    range_high: float,
    range_low: float,
    daily_atr: float,
    profile: RangeBreakoutRetestProfile,
) -> tuple[int, str, float] | None:
    final = min(len(frame) - 1, start_index + profile.maximum_breakout_wait_h4_bars - 1)
    buffer = profile.breakout_buffer_atr * daily_atr
    for index in range(start_index, final + 1):
        row = frame.iloc[index]
        if not np.isfinite(float(row["h4_atr14"])):
            continue
        displacement = float(row["body_h4_atr"])
        location = float(row["close_location"])
        if (
            float(row["mid_close"]) > range_high + buffer
            and displacement >= profile.minimum_breakout_body_h4_atr
            and location >= profile.minimum_breakout_close_location
        ):
            return index, "BUY", range_high
        if (
            float(row["mid_close"]) < range_low - buffer
            and displacement >= profile.minimum_breakout_body_h4_atr
            and location <= 1.0 - profile.minimum_breakout_close_location
        ):
            return index, "SELL", range_low
    return None


def _find_retest(
    frame: pd.DataFrame,
    breakout_index: int,
    side: str,
    level: float,
    daily_atr: float,
    profile: RangeBreakoutRetestProfile,
) -> int | None:
    final = min(
        len(frame) - 2,
        breakout_index + profile.maximum_retest_wait_h4_bars,
    )
    tolerance = profile.retest_tolerance_atr * daily_atr
    for index in range(breakout_index + 1, final + 1):
        row = frame.iloc[index]
        if side == "BUY":
            touched = float(row["mid_low"]) <= level + tolerance
            held = float(row["mid_close"]) > level
            directional = float(row["close_location"]) >= 0.50
        else:
            touched = float(row["mid_high"]) >= level - tolerance
            held = float(row["mid_close"]) < level
            directional = float(row["close_location"]) <= 0.50
        if touched and held and directional:
            return index
    return None


def _simulate_trade(
    *,
    symbol: str,
    profile: RangeBreakoutRetestProfile,
    frame: pd.DataFrame,
    signal: pd.Series,
    breakout_index: int,
    retest_index: int,
    side: str,
    level: float,
) -> dict[str, Any] | None:
    entry_index = retest_index + 1
    if entry_index >= len(frame):
        return None
    entry_bar = frame.iloc[entry_index]
    retest_bar = frame.iloc[retest_index]
    daily_atr = float(signal["atr14"])
    entry = float(entry_bar["ask_open"] if side == "BUY" else entry_bar["bid_open"])

    if side == "BUY":
        structure_stop = min(float(retest_bar["bid_low"]), level) - profile.stop_buffer_atr * daily_atr
        risk_distance = entry - structure_stop
    else:
        structure_stop = max(float(retest_bar["ask_high"]), level) + profile.stop_buffer_atr * daily_atr
        risk_distance = structure_stop - entry

    minimum = profile.minimum_stop_atr * daily_atr
    maximum = profile.maximum_stop_atr * daily_atr
    if not np.isfinite(risk_distance) or risk_distance <= 0 or risk_distance > maximum:
        return None
    risk_distance = max(risk_distance, minimum)
    stop = entry - risk_distance if side == "BUY" else entry + risk_distance
    target = (
        entry + profile.target_r * risk_distance
        if side == "BUY"
        else entry - profile.target_r * risk_distance
    )

    final_index = min(
        len(frame) - 1,
        entry_index + profile.maximum_holding_h4_bars - 1,
    )
    exit_index = final_index
    exit_reason = "TIME"
    exit_price: float | None = None
    for index in range(entry_index, final_index + 1):
        bar = frame.iloc[index]
        outcome = resolve_bar_exit(
            side=side,
            high=float(bar["bid_high"] if side == "BUY" else bar["ask_high"]),
            low=float(bar["bid_low"] if side == "BUY" else bar["ask_low"]),
            stop=stop,
            target=target,
        )
        if outcome is None:
            continue
        exit_index = index
        exit_reason = outcome
        exit_price = stop if outcome == "STOP" else target
        break
    if exit_price is None:
        last = frame.iloc[exit_index]
        exit_price = float(last["bid_close"] if side == "BUY" else last["ask_close"])

    gross_r = (
        (exit_price - entry) / risk_distance
        if side == "BUY"
        else (entry - exit_price) / risk_distance
    )
    base_net_r = gross_r - profile.slippage_reserve_r
    spread_r = float(entry_bar["ask_open"] - entry_bar["bid_open"]) / risk_distance
    breakout = frame.iloc[breakout_index]
    return {
        "engine": "V14_22_RANGE_BREAKOUT_RETEST_SHADOW",
        "family": "D1_RANGE_BREAKOUT_RETEST",
        "mode": "RANGE_BREAKOUT_SHADOW",
        "profile": profile.name,
        "symbol": symbol,
        "side": side,
        "signal_time": pd.Timestamp(signal["time"]),
        "signal_available_at": pd.Timestamp(signal["available_at"]),
        "breakout_time": pd.Timestamp(breakout["time"]) + pd.Timedelta(hours=4),
        "retest_time": pd.Timestamp(retest_bar["time"]) + pd.Timedelta(hours=4),
        "entry_time": pd.Timestamp(entry_bar["time"]),
        "exit_time": pd.Timestamp(frame.iloc[exit_index]["time"]) + pd.Timedelta(hours=4),
        "range_high": float(signal["prior_high20"]),
        "range_low": float(signal["prior_low20"]),
        "breakout_level": float(level),
        "entry_price": entry,
        "exit_price": exit_price,
        "stop_price": stop,
        "target_price": target,
        "signal_atr": daily_atr,
        "signal_adx": float(signal["adx14"]),
        "signal_ema_gap_atr": float(signal["ema_gap_atr"]),
        "signal_range_width_atr": float(signal["range_width_atr"]),
        "signal_atr_to_median": float(signal["atr_to_median"]),
        "breakout_body_h4_atr": float(breakout["body_h4_atr"]),
        "breakout_close_location": float(breakout["close_location"]),
        "risk_distance": float(risk_distance),
        "gross_r_multiple": float(gross_r),
        "embedded_spread_r": float(spread_r),
        "base_slippage_reserve_r": profile.slippage_reserve_r,
        "base_net_r_multiple": float(base_net_r),
        "exit_reason": exit_reason,
        "holding_h4_bars": int(exit_index - entry_index + 1),
        "shadow_only": True,
        "requested_risk_percent": 0.0,
        "executed_risk_percent": 0.0,
        "transmitted": False,
        "promotion_status": "RESEARCH_ONLY",
    }


def generate_profile_trades(
    symbol: str,
    h1_bid: pd.DataFrame,
    h1_ask: pd.DataFrame,
    profile: RangeBreakoutRetestProfile,
) -> pd.DataFrame:
    """Generate chronological shadow trades for one frozen profile."""
    symbol = str(symbol).upper()
    if symbol not in CORE_SYMBOLS:
        raise ValueError(f"Unsupported V14.22 core symbol: {symbol}")
    daily = _daily_range_frame(h1_bid, profile)
    execution = _execution_frame(h1_bid, h1_ask)
    if execution.empty:
        return pd.DataFrame()
    times = pd.to_datetime(execution["time"], utc=True)
    records: list[dict[str, Any]] = []
    next_free_time = pd.Timestamp.min.tz_localize("UTC")
    last_consumed_available = pd.Timestamp.min.tz_localize("UTC")

    for _, signal in daily.loc[daily["range_state"]].iterrows():
        available = pd.Timestamp(signal["available_at"])
        if available <= last_consumed_available or available < next_free_time:
            continue
        eligible = np.flatnonzero((times >= available).to_numpy())
        if not len(eligible):
            continue
        start_index = int(eligible[0])
        breakout = _find_breakout(
            execution,
            start_index,
            float(signal["prior_high20"]),
            float(signal["prior_low20"]),
            float(signal["atr14"]),
            profile,
        )
        if breakout is None:
            continue
        breakout_index, side, level = breakout
        retest_index = _find_retest(
            execution,
            breakout_index,
            side,
            level,
            float(signal["atr14"]),
            profile,
        )
        if retest_index is None:
            last_consumed_available = available
            continue
        trade = _simulate_trade(
            symbol=symbol,
            profile=profile,
            frame=execution,
            signal=signal,
            breakout_index=breakout_index,
            retest_index=retest_index,
            side=side,
            level=level,
        )
        last_consumed_available = available
        if trade is None:
            continue
        records.append(trade)
        next_free_time = pd.Timestamp(trade["exit_time"])

    output = pd.DataFrame(records)
    if not output.empty:
        output = output.sort_values(["entry_time", "symbol", "side"]).reset_index(drop=True)
    return output


def apply_scenario_reserve(
    frame: pd.DataFrame,
    *,
    scenario: str,
    additional_cost_r: float,
) -> pd.DataFrame:
    output = frame.copy()
    output["scenario"] = str(scenario)
    output["scenario_additional_cost_r"] = float(additional_cost_r)
    output["r_multiple"] = (
        pd.to_numeric(output["base_net_r_multiple"], errors="coerce")
        - float(additional_cost_r)
    )
    return output


def profile_configuration(profile: RangeBreakoutRetestProfile) -> dict[str, Any]:
    return {
        **asdict(profile),
        "core_symbols": list(CORE_SYMBOLS),
        "shadow_only": True,
        "requested_risk_percent": 0.0,
        "executed_risk_percent": 0.0,
        "broker_transmission": False,
    }


__all__ = [
    "PROFILES",
    "RangeBreakoutRetestProfile",
    "apply_scenario_reserve",
    "generate_profile_trades",
    "profile_configuration",
]
