"""Independent Gold H4/M30/M15 trend-pullback scanner.

This engine is deliberately shadow-only. Its 10-year development/confirmation
study did not establish positive expectancy after costs, so it may identify and
journal candidates but must not submit orders. That preserves forward evidence
without silently granting an unvalidated strategy account risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from .enums import Signal
from .execution import pip_size
from .gbpusd_breakout_v2 import _adx, _atr, _ema
from .gold_intraday_engine import GOLD_SYMBOL, _completed_rates

ENGINE = "GOLD_TREND_PULLBACK_M30"
AUDIT_SCOPE = "GOLD_PULLBACK_M30"


@dataclass(frozen=True)
class GoldPullbackParams:
    touch_atr: float = 0.40
    adx_min: float = 15.0
    volume_ratio_min: float = 0.80
    m30_body_atr_min: float = 0.15
    m15_body_atr_min: float = 0.10
    stop_atr: float = 1.50
    target_r: float = 2.0
    entry_hours_utc: tuple[int, ...] = tuple(range(7, 18))


@dataclass(frozen=True)
class GoldPullbackSetup:
    side: Signal
    signal_end: datetime
    stop_pips: float
    target_pips: float
    reason: str


@dataclass(frozen=True)
class GoldPullbackEvaluation:
    setup: Optional[GoldPullbackSetup]
    code: str
    reason: str
    signal_end: Optional[datetime]
    facts: dict[str, Any]


def _result(
    setup: Optional[GoldPullbackSetup],
    code: str,
    reason: str,
    signal_end: Optional[datetime],
    facts: dict[str, Any],
) -> GoldPullbackEvaluation:
    return GoldPullbackEvaluation(setup, code, reason, signal_end, facts)


def evaluate_gold_pullback_diagnostic(
    client: Any,
    broker_symbol: str = GOLD_SYMBOL,
    params: GoldPullbackParams = GoldPullbackParams(),
    *,
    completed_m30_shift: int = 0,
) -> GoldPullbackEvaluation:
    """Evaluate completed H4/M30/M15 bars with an explicit rejection trace."""
    shift = max(0, int(completed_m30_shift))
    m30 = _completed_rates(client, broker_symbol, "M30", 1 + shift, 160)
    h4 = _completed_rates(client, broker_symbol, "H4", 1, 140 + shift // 8)
    m15 = _completed_rates(client, broker_symbol, "M15", 1, 48 + shift * 2)
    if m30 is None or h4 is None or m15 is None or len(m30) < 60:
        return _result(
            None,
            "DATA_UNAVAILABLE",
            "Insufficient completed M15, M30, or H4 history.",
            None,
            {"completed_m30_shift": shift},
        )

    m30 = m30.copy()
    m30["atr"] = _atr(m30, 14)
    m30["ema20"] = _ema(m30["close"], 20)
    m30["ema50"] = _ema(m30["close"], 50)
    m30["adx"] = _adx(m30, 14)
    m30["avg_volume"] = m30["tick_volume"].rolling(20, min_periods=20).mean()
    latest = m30.iloc[-1]
    signal_end = latest["time"].to_pydatetime() + pd.Timedelta(minutes=30)

    h4 = h4.copy()
    h4["available"] = h4["time"] + pd.Timedelta(hours=4)
    h4 = h4[h4["available"] <= pd.Timestamp(signal_end)].tail(120)
    m15 = m15.copy()
    m15["end"] = m15["time"] + pd.Timedelta(minutes=15)
    m15 = m15[m15["end"] <= pd.Timestamp(signal_end)]
    if h4.empty or m15.empty or m15.iloc[-1]["end"] != pd.Timestamp(signal_end):
        return _result(
            None,
            "CONTEXT_UNAVAILABLE",
            "Completed H4 trend or matching M15 confirmation was unavailable.",
            signal_end,
            {"completed_m30_shift": shift},
        )

    h4["ema20"] = _ema(h4["close"], 20)
    h4["ema50"] = _ema(h4["close"], 50)
    m15["atr"] = _atr(m15, 14)
    m15["ema20"] = _ema(m15["close"], 20)
    confirm = m15.iloc[-1]
    regime = h4.iloc[-1]
    required = (
        latest["atr"], latest["ema20"], latest["ema50"], latest["adx"],
        latest["avg_volume"], regime["ema20"], regime["ema50"],
        confirm["atr"], confirm["ema20"],
    )
    if any(pd.isna(value) for value in required):
        return _result(
            None,
            "INDICATOR_NOT_READY",
            "One or more completed-candle indicators were unavailable.",
            signal_end,
            {"completed_m30_shift": shift},
        )

    atr = float(latest["atr"])
    volume_ratio = float(latest["tick_volume"] / latest["avg_volume"])
    m30_body_atr = abs(float(latest["close"] - latest["open"])) / atr
    m15_atr = float(confirm["atr"])
    m15_body_atr = (
        abs(float(confirm["close"] - confirm["open"])) / m15_atr
        if m15_atr > 0 else 0.0
    )
    m15_range = float(confirm["high"] - confirm["low"])
    close_location = (
        float(confirm["close"] - confirm["low"]) / m15_range
        if m15_range > 0 else 0.5
    )
    long_bias = bool(
        regime["close"] > regime["ema20"] > regime["ema50"]
        and latest["ema20"] > latest["ema50"]
    )
    short_bias = bool(
        regime["close"] < regime["ema20"] < regime["ema50"]
        and latest["ema20"] < latest["ema50"]
    )
    long_pullback = bool(
        long_bias
        and latest["low"] <= latest["ema20"] + params.touch_atr * atr
        and latest["low"] >= latest["ema50"] - 0.30 * atr
        and latest["close"] > latest["ema20"]
        and latest["close"] > latest["open"]
    )
    short_pullback = bool(
        short_bias
        and latest["high"] >= latest["ema20"] - params.touch_atr * atr
        and latest["high"] <= latest["ema50"] + 0.30 * atr
        and latest["close"] < latest["ema20"]
        and latest["close"] < latest["open"]
    )
    m15_long = bool(
        confirm["close"] > confirm["open"]
        and confirm["close"] > confirm["ema20"]
        and m15_body_atr >= params.m15_body_atr_min
        and close_location >= 0.60
    )
    m15_short = bool(
        confirm["close"] < confirm["open"]
        and confirm["close"] < confirm["ema20"]
        and m15_body_atr >= params.m15_body_atr_min
        and close_location <= 0.40
    )
    facts = {
        "completed_m30_shift": shift,
        "h4_trend": "UP" if long_bias else "DOWN" if short_bias else "NEUTRAL",
        "adx": round(float(latest["adx"]), 2),
        "adx_min": params.adx_min,
        "volume_ratio": round(volume_ratio, 3),
        "volume_ratio_min": params.volume_ratio_min,
        "m30_body_atr": round(m30_body_atr, 3),
        "m15_body_atr": round(m15_body_atr, 3),
        "m30_pullback": (
            "BUY" if long_pullback else "SELL" if short_pullback else "NONE"
        ),
        "m15_confirmation": (
            "BUY" if m15_long else "SELL" if m15_short else "NONE"
        ),
        "execution_authority": "SHADOW_ONLY",
    }
    if signal_end.hour not in params.entry_hours_utc:
        return _result(
            None, "OUTSIDE_ENTRY_SESSION",
            f"M30 close hour {signal_end.hour:02d} UTC is outside 07-17 UTC.",
            signal_end, facts,
        )
    if float(latest["adx"]) < params.adx_min:
        return _result(
            None, "ADX_BELOW_MINIMUM", "M30 ADX did not pass.", signal_end, facts
        )
    if volume_ratio < params.volume_ratio_min:
        return _result(
            None, "VOLUME_BELOW_MINIMUM",
            "M30 tick-volume ratio did not pass.", signal_end, facts,
        )
    if m30_body_atr < params.m30_body_atr_min:
        return _result(
            None, "M30_BODY_TOO_SMALL",
            "M30 reclaim candle body was too small.", signal_end, facts,
        )
    if not long_bias and not short_bias:
        return _result(
            None, "H4_TREND_NEUTRAL",
            "Completed H4 and M30 EMA trends were not aligned.", signal_end, facts,
        )
    if not long_pullback and not short_pullback:
        return _result(
            None, "NO_M30_PULLBACK_RECLAIM",
            "M30 did not pull back to and reclaim its EMA20.", signal_end, facts,
        )
    side = Signal.BUY if long_pullback else Signal.SELL
    if (side is Signal.BUY and not m15_long) or (
        side is Signal.SELL and not m15_short
    ):
        return _result(
            None, "M15_CONFIRMATION_FAILED",
            "The completed M15 candle did not confirm the M30 direction.",
            signal_end, facts,
        )

    pip = pip_size(client, broker_symbol) or 0.1
    stop_pips = params.stop_atr * atr / pip
    reason = (
        f"Shadow {side.value}: H4/M30 trend aligned, M30 EMA20 reclaim, "
        f"M15 confirmed; ADX={latest['adx']:.1f}; vol={volume_ratio:.2f}x"
    )
    setup = GoldPullbackSetup(
        side=side,
        signal_end=signal_end,
        stop_pips=round(stop_pips, 1),
        target_pips=round(params.target_r * stop_pips, 1),
        reason=reason,
    )
    return _result(setup, "SHADOW_SETUP_READY", reason, signal_end, facts)
