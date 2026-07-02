"""Quality-first GBPUSD Satellite V2.

Two independently parameterized intraday setups complement the frozen V4 swing
engine:

* London pullback: H1/M30 trend, M15 pullback, momentum resumption.
* New York retest: H1 trend, London-range breakout, M15 retest.

The module uses completed candles only. It never forces a minimum number of
trades: the 5-15 trades/week figure is an operating target, not a quota.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .enums import Signal
from .execution import pip_size
from .trade_manager import close_position, modify_position_sl
from .gbpusd_v4 import _adx, _atr, _ema, _news_blocked, _rates

MAGIC = 260731
COMMENT = "GBPUSD Satellite V2"


@dataclass(frozen=True)
class LondonParams:
    session_start_hour_utc: int = 7
    session_end_hour_utc: int = 12
    h1_adx_min: float = 10.0
    body_ratio_min: float = 0.50
    volume_ratio_min: float = 0.80
    long_rsi_min: float = 52.0
    short_rsi_max: float = 48.0
    pullback_lookback: int = 5
    pullback_depth_atr: float = 0.20
    stop_atr: float = 1.75
    min_stop_pips: float = 5.0
    max_stop_pips: float = 30.0
    target_r: float = 1.75
    break_even_r: float = 1.0
    max_hold_m15_bars: int = 32


@dataclass(frozen=True)
class NewYorkParams:
    session_start_hour_utc: int = 12
    session_end_hour_utc: int = 17
    h1_adx_min: float = 20.0
    body_ratio_min: float = 0.10
    volume_ratio_min: float = 0.80
    long_rsi_min: float = 54.0
    short_rsi_max: float = 46.0
    breakout_lookback: int = 6
    retest_tolerance_atr: float = 0.0
    stop_atr: float = 1.25
    min_stop_pips: float = 6.0
    max_stop_pips: float = 30.0
    target_r: float = 3.0
    break_even_r: float = 1.5
    max_hold_m15_bars: int = 20


@dataclass(frozen=True)
class SatelliteV2Params:
    base_lot: float = 0.08
    max_risk_percent_per_trade: float = 0.25
    daily_new_risk_percent: float = 0.50
    open_risk_cap_percent: float = 0.50
    max_spread_pips: float = 1.50
    force_flat_hour_utc: int = 20
    max_entries_per_day: int = 2
    london: LondonParams = LondonParams()
    new_york: NewYorkParams = NewYorkParams()


@dataclass(frozen=True)
class SatelliteV2Setup:
    side: Signal
    name: str
    signal_end: datetime
    atr_price: float
    stop_atr: float
    min_stop_pips: float
    max_stop_pips: float
    target_r: float
    break_even_r: float
    max_hold_m15_bars: int
    reason: str


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    return 100 - (100 / (1 + relative_strength))


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["atr14"] = _atr(result, 14)
    result["rsi14"] = _rsi(result["close"], 14)
    result["ema20"] = _ema(result["close"], 20)
    result["ema50"] = _ema(result["close"], 50)
    result["adx14"] = _adx(result, 14)
    result["average_volume"] = result["tick_volume"].rolling(20, min_periods=20).mean()
    result["volume_ratio"] = result["tick_volume"] / result["average_volume"]
    candle_range = (result["high"] - result["low"]).replace(0, np.nan)
    result["body_ratio"] = (result["close"] - result["open"]).abs() / candle_range
    result["bar_end"] = result["time"] + pd.to_timedelta(
        15 if len(result) and (result["time"].iloc[-1].minute % 30) else 15,
        unit="m",
    )
    return result


def _completed_frames(client, symbol: str) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    m15 = _rates(client, symbol, "M15", 420)
    m30 = _rates(client, symbol, "M30", 240)
    h1 = _rates(client, symbol, "H1", 240)
    if m15 is None or m30 is None or h1 is None:
        return None, None, None
    if len(m15) < 120 or len(m30) < 80 or len(h1) < 80:
        return None, None, None
    return _features(m15), _features(m30), _features(h1)


def _trend(anchor: pd.Series, adx_min: float) -> tuple[bool, bool]:
    long_trend = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= adx_min
    )
    short_trend = (
        anchor["ema20"] < anchor["ema50"]
        and anchor["close"] < anchor["ema20"]
        and anchor["adx14"] >= adx_min
    )
    return bool(long_trend), bool(short_trend)


def _london_setup(
    m15: pd.DataFrame,
    m30: pd.DataFrame,
    h1: pd.DataFrame,
    params: LondonParams,
) -> Optional[SatelliteV2Setup]:
    row = m15.iloc[-1]
    signal_end = (row["time"] + pd.Timedelta(minutes=15)).to_pydatetime()
    session_time = signal_end.hour + signal_end.minute / 60
    if not params.session_start_hour_utc <= session_time < params.session_end_hour_utc:
        return None
    if signal_end.weekday() >= 5:
        return None

    anchor = h1.iloc[-1]
    context = m30.iloc[-1]
    long_h1, short_h1 = _trend(anchor, params.h1_adx_min)
    long_context = (
        context["ema20"] > context["ema50"] and context["close"] > context["ema20"]
    )
    short_context = (
        context["ema20"] < context["ema50"] and context["close"] < context["ema20"]
    )
    recent = m15.iloc[-(params.pullback_lookback + 1):-1]
    if len(recent) < params.pullback_lookback:
        return None
    long_pullback = bool(
        (
            (recent["low"] <= recent["ema20"] - params.pullback_depth_atr * recent["atr14"])
            | (recent["close"] < recent["ema20"])
        ).any()
    )
    short_pullback = bool(
        (
            (recent["high"] >= recent["ema20"] + params.pullback_depth_atr * recent["atr14"])
            | (recent["close"] > recent["ema20"])
        ).any()
    )
    previous_high = m15["high"].iloc[-3:-1].max()
    previous_low = m15["low"].iloc[-3:-1].min()
    quality = (
        row["body_ratio"] >= params.body_ratio_min
        and row["volume_ratio"] >= params.volume_ratio_min
    )
    long_trigger = (
        quality and long_h1 and long_context and long_pullback
        and row["close"] > row["ema20"] and row["close"] > row["open"]
        and row["rsi14"] >= params.long_rsi_min and row["close"] > previous_high
    )
    short_trigger = (
        quality and short_h1 and short_context and short_pullback
        and row["close"] < row["ema20"] and row["close"] < row["open"]
        and row["rsi14"] <= params.short_rsi_max and row["close"] < previous_low
    )
    if not long_trigger and not short_trigger:
        return None
    side = Signal.BUY if long_trigger else Signal.SELL
    reason = (
        "London M15 pullback resumed in the H1/M30 trend with strong body, "
        "volume, RSI and two-bar momentum confirmation."
    )
    return SatelliteV2Setup(
        side=side,
        name="LONDON_PULLBACK_V2",
        signal_end=signal_end,
        atr_price=float(row["atr14"]),
        stop_atr=params.stop_atr,
        min_stop_pips=params.min_stop_pips,
        max_stop_pips=params.max_stop_pips,
        target_r=params.target_r,
        break_even_r=params.break_even_r,
        max_hold_m15_bars=params.max_hold_m15_bars,
        reason=reason,
    )


def _new_york_setup(
    m15: pd.DataFrame,
    h1: pd.DataFrame,
    params: NewYorkParams,
) -> Optional[SatelliteV2Setup]:
    row = m15.iloc[-1]
    signal_end = (row["time"] + pd.Timedelta(minutes=15)).to_pydatetime()
    session_time = signal_end.hour + signal_end.minute / 60
    if not params.session_start_hour_utc <= session_time < params.session_end_hour_utc:
        return None
    if signal_end.weekday() >= 5:
        return None

    anchor = h1.iloc[-1]
    long_h1, short_h1 = _trend(anchor, params.h1_adx_min)
    ends = m15["time"] + pd.Timedelta(minutes=15)
    same_day = ends.dt.date == signal_end.date()
    london_window = m15[
        same_day & (ends.dt.hour >= 7) & (ends.dt.hour < 12)
    ]
    if london_window.empty:
        return None
    london_high = float(london_window["high"].max())
    london_low = float(london_window["low"].min())
    recent = m15.iloc[-(params.breakout_lookback + 1):-1]
    if len(recent) < params.breakout_lookback:
        return None
    broke_high = bool((recent["close"] > london_high).any())
    broke_low = bool((recent["close"] < london_low).any())
    tolerance = params.retest_tolerance_atr * row["atr14"]
    quality = (
        row["body_ratio"] >= params.body_ratio_min
        and row["volume_ratio"] >= params.volume_ratio_min
    )
    long_trigger = (
        quality and long_h1 and broke_high
        and row["low"] <= london_high + tolerance
        and row["close"] > london_high and row["close"] > row["open"]
        and row["rsi14"] >= params.long_rsi_min
    )
    short_trigger = (
        quality and short_h1 and broke_low
        and row["high"] >= london_low - tolerance
        and row["close"] < london_low and row["close"] < row["open"]
        and row["rsi14"] <= params.short_rsi_max
    )
    if not long_trigger and not short_trigger:
        return None
    side = Signal.BUY if long_trigger else Signal.SELL
    reason = (
        "New York M15 retest held beyond the completed London range in the H1 "
        "trend with volume and RSI confirmation."
    )
    return SatelliteV2Setup(
        side=side,
        name="NEW_YORK_RETEST_V2",
        signal_end=signal_end,
        atr_price=float(row["atr14"]),
        stop_atr=params.stop_atr,
        min_stop_pips=params.min_stop_pips,
        max_stop_pips=params.max_stop_pips,
        target_r=params.target_r,
        break_even_r=params.break_even_r,
        max_hold_m15_bars=params.max_hold_m15_bars,
        reason=reason,
    )


def evaluate_setup(
    client,
    symbol: str,
    params: SatelliteV2Params = SatelliteV2Params(),
) -> tuple[Optional[SatelliteV2Setup], dict]:
    diagnostics = {
        "m15_signal": Signal.WAIT.value,
        "m30_signal": Signal.WAIT.value,
        "h1_signal": Signal.WAIT.value,
        "setup": None,
        "reason": "Waiting for a completed London pullback or New York retest.",
    }
    if symbol.upper() != "GBPUSD":
        diagnostics["reason"] = "Satellite V2 is restricted to GBPUSD."
        return None, diagnostics
    m15, m30, h1 = _completed_frames(client, symbol)
    if m15 is None or m30 is None or h1 is None:
        diagnostics["reason"] = "Insufficient completed M15/M30/H1 history."
        return None, diagnostics
    current_hour = (m15.iloc[-1]["time"] + pd.Timedelta(minutes=15)).hour
    setup = None
    if params.london.session_start_hour_utc <= current_hour < params.london.session_end_hour_utc:
        setup = _london_setup(m15, m30, h1, params.london)
    elif params.new_york.session_start_hour_utc <= current_hour < params.new_york.session_end_hour_utc:
        setup = _new_york_setup(m15, h1, params.new_york)
    anchor = h1.iloc[-1]
    context = m30.iloc[-1]
    diagnostics["h1_signal"] = (
        Signal.BUY.value if anchor["ema20"] > anchor["ema50"] else Signal.SELL.value
    )
    diagnostics["m30_signal"] = (
        Signal.BUY.value if context["ema20"] > context["ema50"] else Signal.SELL.value
    )
    if setup:
        diagnostics.update({
            "m15_signal": setup.side.value,
            "setup": setup.name,
            "reason": setup.reason,
        })
    return setup, diagnostics


def setup_stop_target_pips(
    setup: SatelliteV2Setup,
    pip: float,
) -> tuple[float, float]:
    stop = setup.stop_atr * setup.atr_price / pip
    stop = min(max(stop, setup.min_stop_pips), setup.max_stop_pips)
    return float(stop), float(stop * setup.target_r)


def current_spread_pips(client, symbol: str) -> Optional[float]:
    info = client.symbol_info(symbol)
    tick = client.symbol_info_tick(symbol)
    if info is None or tick is None:
        return None
    point = float(getattr(info, "point", 0.00001) or 0.00001)
    digits = int(getattr(info, "digits", 5) or 5)
    pip = point * 10 if digits in (3, 5) else point
    return float((tick.ask - tick.bid) / pip) if pip > 0 else None


def risk_capped_lot(
    client,
    symbol: str,
    balance: float,
    stop_pips: float,
    pip_value_per_lot: float,
    params: SatelliteV2Params = SatelliteV2Params(),
) -> tuple[float, float]:
    info = client.symbol_info(symbol)
    minimum = float(getattr(info, "volume_min", 0.01) or 0.01)
    step = float(getattr(info, "volume_step", 0.01) or 0.01)
    maximum = float(getattr(info, "volume_max", params.base_lot) or params.base_lot)
    requested_base = min(
        max(float(os.getenv("SATELLITE_V2_BASE_LOT", params.base_lot)), minimum),
        params.base_lot,
        maximum,
    )
    risk_cap = balance * params.max_risk_percent_per_trade / 100
    risk_lot = risk_cap / (stop_pips * pip_value_per_lot)
    raw = min(requested_base, risk_lot)
    volume = math.floor(raw / step) * step
    volume = min(max(volume, minimum), maximum)
    actual_risk = stop_pips * pip_value_per_lot * volume
    return round(volume, 2), actual_risk


def news_blocked(now: datetime) -> bool:
    class NewsParams:
        news_minutes_before = 10
        news_minutes_after = 10
    return _news_blocked(now, NewsParams())


def manage_positions(
    client,
    symbol: str,
    params: SatelliteV2Params = SatelliteV2Params(),
    now_utc: Optional[datetime] = None,
) -> list[str]:
    messages: list[str] = []
    now_utc = now_utc or datetime.now(timezone.utc)
    positions = [
        position for position in (client.positions_get(symbol=symbol) or [])
        if getattr(position, "magic", None) == MAGIC
    ]
    m15 = _rates(client, symbol, "M15", 220)
    if m15 is None:
        return messages
    for position in positions:
        comment = str(getattr(position, "comment", "")).upper()
        is_london = "LONDON" in comment
        setup_params = params.london if is_london else params.new_york
        target_r = setup_params.target_r
        break_even_r = setup_params.break_even_r
        max_bars = setup_params.max_hold_m15_bars
        entry = float(position.price_open)
        tp = float(getattr(position, "tp", 0.0) or 0.0)
        sl = float(getattr(position, "sl", 0.0) or 0.0)
        initial_risk = abs(tp - entry) / target_r if tp else abs(entry - sl)
        is_buy = position.type == client.POSITION_TYPE_BUY
        favorable = (
            float(position.price_current) - entry
            if is_buy else entry - float(position.price_current)
        )
        entered = pd.to_datetime(position.time, unit="s", utc=True)
        held_bars = len(m15[m15["time"] >= entered])
        must_close = (
            held_bars >= max_bars
            or now_utc.hour >= params.force_flat_hour_utc
            or now_utc.date() > entered.date()
        )
        if must_close:
            ok, message = close_position(client, position.ticket)
            messages.append(
                f"[SATELLITE_V2] {'Closed' if ok else 'Close failed'}: {message}"
            )
            continue
        if initial_risk > 0 and favorable >= break_even_r * initial_risk:
            should_move = (is_buy and (not sl or sl < entry)) or (
                not is_buy and (not sl or sl > entry)
            )
            if should_move:
                ok, message = modify_position_sl(client, position, entry)
                messages.append(
                    f"[SATELLITE_V2] {'Break-even set' if ok else 'Break-even failed'}: {message}"
                )
    return messages
