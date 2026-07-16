"""V14.6.1 multi-entry intraday ICT trend research engine.

This module is deliberately broker-free.  It generates completed-H1 signals
for GBPUSD, GBPJPY and AUDUSD, enters on the next H1 open, and permits several
qualified entries in the same day.  The design combines:

* H1 EMA trend alignment and slope;
* completed H4 directional confirmation;
* London/New York/Asia-London session controls;
* pullback-reclaim and range-break continuation entries;
* ATR-normalized stops;
* optional partial profit at 1R with break-even protection;
* per-profile cooldown and daily entry limits.

It is research code only.  No MT5 order or account API is imported here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from mt5_ai_bridge.v14_3_all_symbol_ict import atr, ema, prepare_frames


@dataclass(frozen=True)
class IntradayTrendProfile:
    name: str
    signal_mode: str
    session_start_hour: int
    session_end_hour: int
    fast_ema: int
    medium_ema: int
    slow_ema: int
    breakout_lookback: int
    pullback_atr: float
    minimum_body_atr: float
    minimum_trend_atr: float
    stop_atr: float
    target_r: float
    max_holding_hours: int
    cooldown_hours: int = 1
    max_trades_per_day: int = 5
    require_h4_bias: bool = True
    partial_fraction: float = 0.50
    partial_target_r: float = 1.00
    move_to_break_even: bool = True
    allowed_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)


PROFILES: dict[str, tuple[IntradayTrendProfile, ...]] = {
    "GBPUSD": (
        IntradayTrendProfile(
            "gu_london_pullback_partial", "PULLBACK", 6, 13, 8, 21, 55,
            4, 0.30, 0.16, 0.10, 0.90, 1.75, 7, 1, 5, True, 0.50, 1.00, True,
        ),
        IntradayTrendProfile(
            "gu_ny_breakout_partial", "BREAKOUT", 12, 18, 8, 21, 55,
            5, 0.20, 0.22, 0.12, 1.00, 2.00, 8, 1, 4, True, 0.50, 1.00, True,
        ),
        IntradayTrendProfile(
            "gu_intraday_reclaim", "RECLAIM", 7, 18, 5, 13, 34,
            3, 0.40, 0.12, 0.08, 0.80, 1.35, 5, 1, 6, False, 0.40, 0.80, True,
        ),
    ),
    "GBPJPY": (
        IntradayTrendProfile(
            "gj_london_breakout_partial", "BREAKOUT", 6, 13, 8, 21, 55,
            4, 0.25, 0.24, 0.13, 1.10, 1.75, 7, 1, 5, True, 0.50, 1.00, True,
        ),
        IntradayTrendProfile(
            "gj_london_pullback", "PULLBACK", 7, 14, 8, 21, 55,
            4, 0.40, 0.18, 0.11, 1.05, 1.50, 6, 1, 5, True, 0.50, 0.90, True,
        ),
        IntradayTrendProfile(
            "gj_ny_reclaim", "RECLAIM", 12, 19, 5, 13, 34,
            3, 0.45, 0.14, 0.09, 0.95, 1.35, 5, 1, 5, False, 0.40, 0.80, True,
        ),
    ),
    "AUDUSD": (
        IntradayTrendProfile(
            "au_asia_london_pullback", "PULLBACK", 1, 11, 8, 21, 55,
            4, 0.35, 0.14, 0.08, 0.85, 1.50, 7, 1, 6, True, 0.50, 0.90, True,
        ),
        IntradayTrendProfile(
            "au_london_breakout", "BREAKOUT", 6, 13, 8, 21, 55,
            5, 0.20, 0.18, 0.10, 0.90, 1.75, 7, 1, 5, True, 0.50, 1.00, True,
        ),
        IntradayTrendProfile(
            "au_intraday_reclaim", "RECLAIM", 2, 17, 5, 13, 34,
            3, 0.45, 0.10, 0.06, 0.75, 1.25, 5, 1, 7, False, 0.40, 0.75, True,
        ),
    ),
}


def validate_profiles() -> None:
    for symbol, profiles in PROFILES.items():
        if symbol not in {"GBPUSD", "GBPJPY", "AUDUSD"}:
            raise RuntimeError(f"Unsupported V14.6.1 symbol: {symbol}")
        for profile in profiles:
            if profile.signal_mode not in {"PULLBACK", "BREAKOUT", "RECLAIM"}:
                raise RuntimeError(f"Invalid signal mode: {profile.signal_mode}")
            if not (0 <= profile.session_start_hour < profile.session_end_hour <= 24):
                raise RuntimeError(f"Invalid session for {profile.name}")
            if not (0.0 < profile.partial_fraction < 1.0):
                raise RuntimeError(f"Invalid partial fraction for {profile.name}")
            if profile.max_trades_per_day < 2:
                raise RuntimeError("Intraday profiles must permit multiple daily entries")
            if profile.cooldown_hours < 1:
                raise RuntimeError("H1 profiles require at least a one-bar cooldown")


validate_profiles()


def _prepare(h1: pd.DataFrame, h4: pd.DataFrame, d1: pd.DataFrame, profile: IntradayTrendProfile) -> pd.DataFrame:
    frame, _, _ = prepare_frames(h1, h4, d1)
    frame = frame.copy().sort_values("time").reset_index(drop=True)
    frame["ema_fast"] = ema(frame["close"], profile.fast_ema)
    frame["ema_medium"] = ema(frame["close"], profile.medium_ema)
    frame["ema_slow"] = ema(frame["close"], profile.slow_ema)
    frame["atr14"] = atr(frame, 14)
    frame["body_atr"] = (frame["close"] - frame["open"]).abs() / frame["atr14"].replace(0, np.nan)
    frame["trend_atr"] = (frame["ema_medium"] - frame["ema_slow"]).abs() / frame["atr14"].replace(0, np.nan)
    frame["medium_slope"] = frame["ema_medium"] - frame["ema_medium"].shift(3)
    frame["prior_high"] = frame["high"].rolling(profile.breakout_lookback).max().shift(1)
    frame["prior_low"] = frame["low"].rolling(profile.breakout_lookback).min().shift(1)
    frame["previous_high"] = frame["high"].shift(1)
    frame["previous_low"] = frame["low"].shift(1)
    frame["previous_close"] = frame["close"].shift(1)
    frame["previous_fast"] = frame["ema_fast"].shift(1)
    return frame


def _signals(frame: pd.DataFrame, profile: IntradayTrendProfile) -> tuple[pd.Series, pd.Series]:
    hours = frame["time"].dt.hour
    weekdays = frame["time"].dt.weekday
    in_session = (
        (hours >= profile.session_start_hour)
        & (hours < profile.session_end_hour)
        & weekdays.isin(profile.allowed_weekdays)
    )
    liquid = frame["atr14"].notna() & (frame["atr14"] > 0)
    strength = frame["trend_atr"] >= profile.minimum_trend_atr
    body = frame["body_atr"] >= profile.minimum_body_atr

    h1_long = (
        (frame["ema_fast"] > frame["ema_medium"])
        & (frame["ema_medium"] > frame["ema_slow"])
        & (frame["medium_slope"] > 0)
    )
    h1_short = (
        (frame["ema_fast"] < frame["ema_medium"])
        & (frame["ema_medium"] < frame["ema_slow"])
        & (frame["medium_slope"] < 0)
    )
    h4_long = (frame["h4_close"] > frame["h4_ema20"]) & (frame["h4_ema20"] > frame["h4_ema50"])
    h4_short = (frame["h4_close"] < frame["h4_ema20"]) & (frame["h4_ema20"] < frame["h4_ema50"])
    if not profile.require_h4_bias:
        h4_long = h4_short = pd.Series(True, index=frame.index)

    if profile.signal_mode == "BREAKOUT":
        long_trigger = (
            (frame["close"] > frame["prior_high"])
            & (frame["close"] > frame["open"])
            & (frame["close"] > frame["ema_fast"])
        )
        short_trigger = (
            (frame["close"] < frame["prior_low"])
            & (frame["close"] < frame["open"])
            & (frame["close"] < frame["ema_fast"])
        )
    elif profile.signal_mode == "PULLBACK":
        tolerance = profile.pullback_atr * frame["atr14"]
        long_trigger = (
            (frame["low"] <= frame["ema_medium"] + tolerance)
            & (frame["close"] > frame["ema_fast"])
            & (frame["close"] > frame["open"])
            & (frame["close"] > frame["previous_high"])
        )
        short_trigger = (
            (frame["high"] >= frame["ema_medium"] - tolerance)
            & (frame["close"] < frame["ema_fast"])
            & (frame["close"] < frame["open"])
            & (frame["close"] < frame["previous_low"])
        )
    else:  # RECLAIM
        tolerance = profile.pullback_atr * frame["atr14"]
        long_trigger = (
            (frame["previous_close"] <= frame["previous_fast"] + tolerance)
            & (frame["close"] > frame["ema_fast"])
            & (frame["close"] > frame["open"])
        )
        short_trigger = (
            (frame["previous_close"] >= frame["previous_fast"] - tolerance)
            & (frame["close"] < frame["ema_fast"])
            & (frame["close"] < frame["open"])
        )

    common = in_session & liquid & strength & body
    return common & h1_long & h4_long & long_trigger, common & h1_short & h4_short & short_trigger


def simulate_partial_exit(
    frame: pd.DataFrame,
    signal_index: int,
    side: int,
    stop_price: float,
    target_r: float,
    max_holding_hours: int,
    partial_fraction: float,
    partial_target_r: float,
    move_to_break_even: bool,
) -> tuple[pd.Timestamp, float]:
    """Conservative partial-profit simulation entered on the next H1 open.

    When stop and target are touched in the same candle, stop is assumed first.
    After the partial target, the residual position moves to break-even when the
    profile requests it.
    """
    if signal_index + 1 >= len(frame):
        return pd.Timestamp(frame.iloc[signal_index]["end"]), 0.0
    entry_row = frame.iloc[signal_index + 1]
    entry = float(entry_row["open"])
    initial_risk = (entry - stop_price) * side
    if not np.isfinite(initial_risk) or initial_risk <= 0:
        return pd.Timestamp(entry_row["end"]), 0.0

    partial_price = entry + side * partial_target_r * initial_risk
    final_price = entry + side * target_r * initial_risk
    current_stop = float(stop_price)
    partial_taken = False
    realized_r = 0.0
    remaining = 1.0
    last_index = min(len(frame) - 1, signal_index + max_holding_hours)

    for index in range(signal_index + 1, last_index + 1):
        row = frame.iloc[index]
        low, high = float(row["low"]), float(row["high"])
        stop_hit = low <= current_stop if side > 0 else high >= current_stop
        if stop_hit:
            residual_r = (current_stop - entry) * side / initial_risk
            return pd.Timestamp(row["end"]), float(realized_r + remaining * residual_r)

        if not partial_taken:
            partial_hit = high >= partial_price if side > 0 else low <= partial_price
            if partial_hit:
                realized_r += partial_fraction * partial_target_r
                remaining = 1.0 - partial_fraction
                partial_taken = True
                if move_to_break_even:
                    current_stop = entry

        final_hit = high >= final_price if side > 0 else low <= final_price
        if final_hit:
            return pd.Timestamp(row["end"]), float(realized_r + remaining * target_r)

    final = frame.iloc[last_index]
    residual_r = (float(final["close"]) - entry) * side / initial_risk
    return pd.Timestamp(final["end"]), float(realized_r + remaining * residual_r)


def generate_intraday_candidates(
    symbol: str,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    d1: pd.DataFrame,
    profile: IntradayTrendProfile,
) -> pd.DataFrame:
    symbol = str(symbol).upper()
    if symbol not in PROFILES:
        raise ValueError(f"Unsupported V14.6.1 intraday symbol: {symbol}")
    frame = _prepare(h1, h4, d1, profile)
    long_signal, short_signal = _signals(frame, profile)

    rows: list[dict] = []
    day_counts: dict[pd.Timestamp, int] = {}
    last_entry_by_side: dict[int, pd.Timestamp] = {}
    indices = np.flatnonzero((long_signal | short_signal).to_numpy())
    for raw_index in indices:
        index = int(raw_index)
        row = frame.iloc[index]
        signal_time = pd.Timestamp(row["end"])
        day = signal_time.floor("D")
        if day_counts.get(day, 0) >= profile.max_trades_per_day:
            continue
        side = 1 if bool(long_signal.iloc[index]) else -1
        previous = last_entry_by_side.get(side)
        if previous is not None and signal_time < previous + pd.Timedelta(hours=profile.cooldown_hours):
            continue
        atr_value = float(row["atr14"])
        if not np.isfinite(atr_value) or atr_value <= 0:
            continue
        if side > 0:
            stop = min(float(row["low"]), float(row["ema_medium"]) - profile.stop_atr * atr_value)
        else:
            stop = max(float(row["high"]), float(row["ema_medium"]) + profile.stop_atr * atr_value)
        exit_time, result_r = simulate_partial_exit(
            frame,
            index,
            side,
            stop,
            profile.target_r,
            profile.max_holding_hours,
            profile.partial_fraction,
            profile.partial_target_r,
            profile.move_to_break_even,
        )
        engine = f"{symbol}_ICT_INTRADAY_{profile.name.upper()}"
        rows.append(
            {
                "symbol": symbol,
                "engine": engine,
                "setup": f"v14_6_1_{symbol.lower()}_{profile.name}",
                "profile": profile.name,
                "side": "BUY" if side > 0 else "SELL",
                "entry_time": signal_time,
                "exit_time": exit_time,
                "r_multiple": float(result_r),
                "signal_atr": atr_value,
                "signal_mode": profile.signal_mode,
                "partial_fraction": profile.partial_fraction,
                "partial_target_r": profile.partial_target_r,
                "target_r": profile.target_r,
                "max_trades_per_day": profile.max_trades_per_day,
            }
        )
        day_counts[day] = day_counts.get(day, 0) + 1
        last_entry_by_side[side] = signal_time

    if not rows:
        return pd.DataFrame(
            columns=[
                "symbol", "engine", "setup", "profile", "side", "entry_time",
                "exit_time", "r_multiple", "signal_atr", "signal_mode",
                "partial_fraction", "partial_target_r", "target_r",
                "max_trades_per_day",
            ]
        )
    output = pd.DataFrame(rows)
    return output.sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)


def generate_symbol_profiles(
    symbol: str,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    d1: pd.DataFrame,
    profiles: Iterable[IntradayTrendProfile] | None = None,
) -> pd.DataFrame:
    selected_profiles = tuple(profiles or PROFILES[str(symbol).upper()])
    frames = [
        generate_intraday_candidates(symbol, h1, h4, d1, profile)
        for profile in selected_profiles
    ]
    usable = [frame for frame in frames if not frame.empty]
    if not usable:
        return pd.DataFrame()
    output = pd.concat(usable, ignore_index=True, sort=False)
    return output.drop_duplicates(
        ["entry_time", "exit_time", "symbol", "engine", "side"]
    ).sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)
