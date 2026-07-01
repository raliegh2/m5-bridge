"""GBPUSD H1/M30 intraday satellite engine.

The satellite complements the frozen V4 D1/H4 swing engine. It only evaluates
completed candles, trades London/New York hours, risks 0.10-0.15% per position,
and is flat by the end of the trading day. It is a separate strategy version;
it does not modify the frozen V4 rules.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .enums import Signal
from .trade_manager import close_position
from .gbpusd_v4 import _adx, _atr, _ema, _news_blocked, _rates

MAGIC = 260730
COMMENT = "GBPUSD Satellite Intraday"


@dataclass(frozen=True)
class SatelliteParams:
    risk_percent: float = 0.12
    h1_adx_min: float = 20.0
    channel_bars: int = 12
    body_ratio_min: float = 0.40
    volume_ratio_min: float = 1.00
    long_rsi_min: float = 55.0
    short_rsi_max: float = 45.0
    session_start_hour_utc: int = 9
    session_end_hour_utc: int = 17
    force_flat_hour_utc: int = 20
    stop_atr: float = 1.75
    target_r: float = 1.75
    min_stop_pips: float = 8.0
    max_stop_pips: float = 35.0
    max_hold_m30_bars: int = 12
    max_spread_pips: float = 2.0
    max_entries_per_day: int = 1


@dataclass(frozen=True)
class SatelliteSetup:
    side: Signal
    signal_end: datetime
    atr_price: float
    reason: str
    h1_adx: float
    rsi: float
    volume_ratio: float
    body_ratio: float


def _state_path() -> Path:
    return Path(os.getenv("SATELLITE_STATE_PATH", "satellite_state.json"))


def load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {"last_signal_end": None, "last_entry_date": None, "positions": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    payload.setdefault("last_signal_end", None)
    payload.setdefault("last_entry_date", None)
    payload.setdefault("positions", {})
    return payload


def save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    return 100 - (100 / (1 + relative_strength))


def _completed_h1_from_m30(m30: pd.DataFrame) -> pd.DataFrame:
    frame = m30.copy().set_index("time")
    h1 = frame.resample("1h", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), tick_volume=("tick_volume", "sum"),
    ).dropna()
    return h1.reset_index()


def evaluate_setup(
    client,
    symbol: str,
    params: SatelliteParams = SatelliteParams(),
) -> tuple[Optional[SatelliteSetup], dict]:
    diagnostics = {
        "m30_signal": Signal.WAIT.value,
        "h1_signal": Signal.WAIT.value,
        "reason": "Waiting for a completed M30 London/New York setup.",
        "m30_time": None,
        "h1_time": None,
    }
    if symbol.upper() != "GBPUSD":
        diagnostics["reason"] = "Satellite is restricted to GBPUSD."
        return None, diagnostics

    m30 = _rates(client, symbol, "M30", 420)
    if m30 is None or len(m30) < 160:
        diagnostics["reason"] = "Insufficient completed M30 history."
        return None, diagnostics
    h1 = _rates(client, symbol, "H1", 240)
    if h1 is None or len(h1) < 80:
        h1 = _completed_h1_from_m30(m30)
    if len(h1) < 80:
        diagnostics["reason"] = "Insufficient completed H1 history."
        return None, diagnostics

    m30["atr14"] = _atr(m30, 14)
    m30["rsi14"] = _rsi(m30["close"], 14)
    m30["average_volume"] = m30["tick_volume"].rolling(20, min_periods=20).mean()
    m30["volume_ratio"] = m30["tick_volume"] / m30["average_volume"]
    candle_range = (m30["high"] - m30["low"]).replace(0, np.nan)
    m30["body_ratio"] = (m30["close"] - m30["open"]).abs() / candle_range
    m30["channel_high"] = m30["high"].shift(1).rolling(
        params.channel_bars, min_periods=params.channel_bars
    ).max()
    m30["channel_low"] = m30["low"].shift(1).rolling(
        params.channel_bars, min_periods=params.channel_bars
    ).min()

    h1["ema20"] = _ema(h1["close"], 20)
    h1["ema50"] = _ema(h1["close"], 50)
    h1["adx14"] = _adx(h1, 14)
    row = m30.iloc[-1]
    anchor = h1.iloc[-1]
    signal_end = (row["time"] + pd.Timedelta(minutes=30)).to_pydatetime()
    h1_end = (anchor["time"] + pd.Timedelta(hours=1)).to_pydatetime()
    diagnostics["m30_time"] = signal_end.isoformat()
    diagnostics["h1_time"] = h1_end.isoformat()

    required = [
        row["atr14"], row["rsi14"], row["volume_ratio"], row["body_ratio"],
        row["channel_high"], row["channel_low"], anchor["ema20"],
        anchor["ema50"], anchor["adx14"],
    ]
    if any(pd.isna(value) for value in required):
        diagnostics["reason"] = "Satellite indicators are still warming up."
        return None, diagnostics
    if signal_end.weekday() >= 5:
        diagnostics["reason"] = "Weekend entries are disabled."
        return None, diagnostics
    if not params.session_start_hour_utc <= signal_end.hour < params.session_end_hour_utc:
        diagnostics["reason"] = (
            f"Outside satellite session {params.session_start_hour_utc:02d}:00-"
            f"{params.session_end_hour_utc:02d}:00 UTC."
        )
        return None, diagnostics

    long_trend = (
        anchor["ema20"] > anchor["ema50"]
        and anchor["close"] > anchor["ema20"]
        and anchor["adx14"] >= params.h1_adx_min
    )
    short_trend = (
        anchor["ema20"] < anchor["ema50"]
        and anchor["close"] < anchor["ema20"]
        and anchor["adx14"] >= params.h1_adx_min
    )
    diagnostics["h1_signal"] = (
        Signal.BUY.value if long_trend else Signal.SELL.value if short_trend else Signal.WAIT.value
    )
    quality = (
        row["body_ratio"] >= params.body_ratio_min
        and row["volume_ratio"] >= params.volume_ratio_min
    )
    long_entry = (
        long_trend and quality and row["rsi14"] >= params.long_rsi_min
        and row["close"] > row["channel_high"]
    )
    short_entry = (
        short_trend and quality and row["rsi14"] <= params.short_rsi_max
        and row["close"] < row["channel_low"]
    )
    if long_entry:
        diagnostics["m30_signal"] = Signal.BUY.value
        reason = (
            "H1 EMA20/EMA50 uptrend with ADX strength; completed M30 close "
            "broke the prior 12-bar high on strong body and volume."
        )
        diagnostics["reason"] = reason
        return SatelliteSetup(
            Signal.BUY, signal_end, float(row["atr14"]), reason,
            float(anchor["adx14"]), float(row["rsi14"]),
            float(row["volume_ratio"]), float(row["body_ratio"]),
        ), diagnostics
    if short_entry:
        diagnostics["m30_signal"] = Signal.SELL.value
        reason = (
            "H1 EMA20/EMA50 downtrend with ADX strength; completed M30 close "
            "broke the prior 12-bar low on strong body and volume."
        )
        diagnostics["reason"] = reason
        return SatelliteSetup(
            Signal.SELL, signal_end, float(row["atr14"]), reason,
            float(anchor["adx14"]), float(row["rsi14"]),
            float(row["volume_ratio"]), float(row["body_ratio"]),
        ), diagnostics
    diagnostics["reason"] = "H1/M30 satellite conditions are not simultaneously aligned."
    return None, diagnostics


def current_spread_pips(client, symbol: str) -> Optional[float]:
    info = client.symbol_info(symbol)
    tick = client.symbol_info_tick(symbol)
    if info is None or tick is None:
        return None
    point = float(getattr(info, "point", 0.00001) or 0.00001)
    digits = int(getattr(info, "digits", 5) or 5)
    pip = point * 10 if digits in (3, 5) else point
    return float((tick.ask - tick.bid) / pip) if pip > 0 else None


def manage_positions(
    client,
    symbol: str,
    state: dict,
    params: SatelliteParams = SatelliteParams(),
    now_utc: Optional[datetime] = None,
) -> list[str]:
    messages: list[str] = []
    now_utc = now_utc or datetime.now(timezone.utc)
    positions = [
        position for position in (client.positions_get(symbol=symbol) or [])
        if getattr(position, "magic", None) == MAGIC
    ]
    active_tickets = {str(position.ticket) for position in positions}
    state["positions"] = {
        ticket: payload for ticket, payload in state.get("positions", {}).items()
        if ticket in active_tickets
    }
    m30 = _rates(client, symbol, "M30", 160)
    for position in positions:
        ticket = str(position.ticket)
        entered = pd.to_datetime(position.time, unit="s", utc=True)
        record = state["positions"].setdefault(ticket, {"entry_time": entered.isoformat()})
        held_bars = len(m30[m30["time"] >= entered]) if m30 is not None else 0
        session_expired = now_utc.hour >= params.force_flat_hour_utc
        stale_from_prior_day = now_utc.date() > entered.date()
        if held_bars >= params.max_hold_m30_bars or session_expired or stale_from_prior_day:
            ok, message = close_position(client, position.ticket)
            messages.append(
                f"[SATELLITE_INTRADAY] {'Closed' if ok else 'Close failed'}: {message}"
            )
            if ok:
                state["positions"].pop(ticket, None)
        else:
            record["held_m30_bars"] = held_bars
    return messages


def can_enter_today(state: dict, signal_end: datetime) -> bool:
    return state.get("last_entry_date") != signal_end.date().isoformat()


def mark_entry(state: dict, signal_end: datetime) -> None:
    state["last_signal_end"] = signal_end.isoformat()
    state["last_entry_date"] = signal_end.date().isoformat()


def duplicate_signal(state: dict, signal_end: datetime) -> bool:
    return state.get("last_signal_end") == signal_end.isoformat()


def normalized_risk_percent(params: SatelliteParams) -> float:
    env_value = os.getenv("SATELLITE_RISK_PERCENT", "").strip()
    try:
        requested = float(env_value) if env_value else params.risk_percent
    except ValueError:
        requested = params.risk_percent
    return min(max(requested, 0.10), 0.15)


def stop_and_target_pips(
    atr_price: float,
    pip_size: float,
    params: SatelliteParams = SatelliteParams(),
) -> tuple[float, float]:
    stop = params.stop_atr * atr_price / pip_size
    stop = min(max(stop, params.min_stop_pips), params.max_stop_pips)
    return float(stop), float(stop * params.target_r)


def news_blocked(now: datetime, params: SatelliteParams) -> bool:
    class NewsParams:
        news_minutes_before = 10
        news_minutes_after = 10
    return _news_blocked(now, NewsParams())
