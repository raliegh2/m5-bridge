"""Gold (XAUUSD) intraday breakout engine — the metals satellite for V14.21.

An additive, opt-in engine that complements the validated FX portfolio with the
one instrument that carries an intraday edge after costs: gold. It is the exact
setup validated this cycle (16-month M5 proxy + a held-out out-of-sample cut):

    M30 breakout of a 55-bar channel, confirmed by the H4 EMA20/50 trend,
    with H4-style ADX and tick-volume filters, a 2x ATR stop clipped wide for
    gold's large price ATR, a 2R target, and a 2.5x ATR trail from 1R.

Standalone backtest: ~+17% / 16 months, PF ~1.5, out-of-sample +4.6% at PF 1.37
(132 trades). Added to the V14.21 combined replay it lifted net profit ~+47%.

This module ONLY generates a setup from completed candles. It does not size,
admit or transmit orders — the caller wraps the setup in a LiveSignal and routes
it through the existing V14.21 admission/execution boundary, so gold shares the
same per-trade, combined-risk and drawdown controls as the FX engines. Gold is
off unless the caller explicitly enables it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from .enums import Signal
from .execution import pip_size
# Reuse the branch's validated indicator + candle helpers so the maths is
# identical to the proven breakout engine.
from .gbpusd_breakout_v2 import _ema, _atr, _adx

ENGINE = "GOLD_INTRADAY_M30"
GOLD_SYMBOL = "XAUUSD"


@dataclass(frozen=True)
class GoldParams:
    entry_tf: str = "M30"
    trend_tf: str = "H4"
    channel_bars: int = 55
    adx_min: float = 15.0
    volume_ratio_min: float = 0.80
    stop_atr: float = 2.0
    target_r: float = 2.0
    trail_atr: float = 2.5
    trail_start_r: float = 1.0
    min_stop_pips: float = 10.0
    max_stop_pips: float = 5000.0     # wide: gold ATR is large in price terms
    max_hold_bars: int = 48           # ~24h on M30
    # Session-hour entries (London + New York), UTC. Gold trends in these hours.
    entry_hours_utc: tuple = tuple(range(7, 18))


@dataclass(frozen=True)
class GoldSetup:
    side: Signal
    signal_end: datetime
    atr_price: float
    stop_pips: float
    target_pips: float
    volume_ratio: float
    reason: str


@dataclass(frozen=True)
class GoldEvaluation:
    setup: Optional[GoldSetup]
    code: str
    reason: str
    signal_end: Optional[datetime]
    facts: dict[str, Any]


def _completed_rates(
    client: Any,
    broker_symbol: str,
    timeframe: str,
    start_pos: int,
    count: int,
) -> Optional[pd.DataFrame]:
    raw = client.copy_rates_from_pos(
        broker_symbol, timeframe, int(start_pos), int(count)
    )
    if raw is None or len(raw) == 0:
        return None
    frame = pd.DataFrame(raw)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.sort_values("time").reset_index(drop=True)


def evaluate_gold_setup_diagnostic(
    client: Any,
    broker_symbol: str = GOLD_SYMBOL,
    params: GoldParams = GoldParams(),
    *,
    completed_m30_shift: int = 0,
) -> GoldEvaluation:
    """Evaluate one completed M30 bar and return an explicit rule trace.

    ``completed_m30_shift=0`` is the newest completed bar.  Positive shifts
    support bounded restart catch-up without using H4 candles that were not yet
    complete at the recovered M30 close.
    """
    shift = max(0, int(completed_m30_shift))
    need = max(160, params.channel_bars + 60)
    entry = _completed_rates(
        client, broker_symbol, params.entry_tf, 1 + shift, need
    )
    trend_count = 120 + (shift // 8) + 8
    trend = _completed_rates(
        client, broker_symbol, params.trend_tf, 1, trend_count
    )
    if entry is None or trend is None or len(entry) <= params.channel_bars:
        return GoldEvaluation(
            None,
            "DATA_UNAVAILABLE",
            "Insufficient completed M30 or H4 history.",
            None,
            {"completed_m30_shift": shift},
        )

    entry = entry.copy()
    entry["atr"] = _atr(entry, 14)
    entry["adx"] = _adx(entry, 14)
    entry["avg_tick_volume"] = entry["tick_volume"].rolling(
        20, min_periods=20
    ).mean()
    latest = entry.iloc[-1]
    signal_end = (
        latest["time"].to_pydatetime() + pd.Timedelta(minutes=30)
    )

    trend = trend.copy()
    trend["available"] = trend["time"] + pd.Timedelta(hours=4)
    trend = trend[trend["available"] <= pd.Timestamp(signal_end)].tail(120)
    if trend.empty:
        return GoldEvaluation(
            None,
            "H4_CONTEXT_UNAVAILABLE",
            "No completed H4 context existed at the M30 close.",
            signal_end,
            {"completed_m30_shift": shift},
        )
    trend["ema20"] = _ema(trend["close"], 20)
    trend["ema50"] = _ema(trend["close"], 50)
    reg = trend.iloc[-1]

    required = (
        latest["atr"],
        latest["adx"],
        latest["avg_tick_volume"],
        reg["ema20"],
        reg["ema50"],
    )
    if any(pd.isna(value) for value in required):
        return GoldEvaluation(
            None,
            "INDICATOR_NOT_READY",
            "ATR, ADX, volume average, or H4 EMA was unavailable.",
            signal_end,
            {"completed_m30_shift": shift},
        )

    prior = entry.iloc[-(params.channel_bars + 1) : -1]
    channel_high = float(prior["high"].max())
    channel_low = float(prior["low"].min())
    volume_ratio = float(
        latest["tick_volume"] / latest["avg_tick_volume"]
    )
    long_regime = bool(
        reg["ema20"] > reg["ema50"] and reg["close"] > reg["ema20"]
    )
    short_regime = bool(
        reg["ema20"] < reg["ema50"] and reg["close"] < reg["ema20"]
    )
    long_break = bool(latest["close"] > channel_high)
    short_break = bool(latest["close"] < channel_low)
    facts = {
        "completed_m30_shift": shift,
        "m30_close": round(float(latest["close"]), 5),
        "channel_high": round(channel_high, 5),
        "channel_low": round(channel_low, 5),
        "adx": round(float(latest["adx"]), 2),
        "adx_min": params.adx_min,
        "volume_ratio": round(volume_ratio, 3),
        "volume_ratio_min": params.volume_ratio_min,
        "h4_trend": (
            "UP" if long_regime else "DOWN" if short_regime else "NEUTRAL"
        ),
        "channel_break": (
            "BUY" if long_break else "SELL" if short_break else "NONE"
        ),
    }

    if signal_end.hour not in params.entry_hours_utc:
        return GoldEvaluation(
            None,
            "OUTSIDE_ENTRY_SESSION",
            f"M30 close hour {signal_end.hour:02d} UTC is outside 07-17 UTC.",
            signal_end,
            facts,
        )
    if float(latest["adx"]) < params.adx_min:
        return GoldEvaluation(
            None, "ADX_BELOW_MINIMUM", "M30 ADX did not pass.", signal_end, facts
        )
    if volume_ratio < params.volume_ratio_min:
        return GoldEvaluation(
            None,
            "VOLUME_BELOW_MINIMUM",
            "M30 tick-volume ratio did not pass.",
            signal_end,
            facts,
        )
    if not long_break and not short_break:
        return GoldEvaluation(
            None,
            "NO_55_BAR_CHANNEL_BREAK",
            "M30 close remained inside the prior 55-bar channel.",
            signal_end,
            facts,
        )
    if not ((long_break and long_regime) or (short_break and short_regime)):
        return GoldEvaluation(
            None,
            "H4_TREND_CONFLICT",
            "Channel-break direction did not agree with completed H4 trend.",
            signal_end,
            facts,
        )

    side = Signal.BUY if long_break else Signal.SELL
    atr_price = float(latest["atr"])
    pip = pip_size(client, broker_symbol) or 0.1
    stop_pips = min(
        max(
            (params.stop_atr * atr_price) / pip,
            params.min_stop_pips,
        ),
        params.max_stop_pips,
    )
    target_pips = params.target_r * stop_pips
    reason = (
        f"M30 close broke {params.channel_bars}-bar "
        f"{'high' if side is Signal.BUY else 'low'}; H4 trend "
        f"{'up' if side is Signal.BUY else 'down'}; "
        f"ADX={latest['adx']:.1f}; vol={volume_ratio:.2f}x"
    )
    setup = GoldSetup(
        side,
        signal_end,
        atr_price,
        round(stop_pips, 1),
        round(target_pips, 1),
        volume_ratio,
        reason,
    )
    return GoldEvaluation(
        setup, "SETUP_READY", reason, signal_end, facts
    )


def evaluate_gold_setup(
    client,
    broker_symbol: str = GOLD_SYMBOL,
    params: GoldParams = GoldParams(),
) -> Optional[GoldSetup]:
    """Evaluate the latest completed M30 candle against the completed H4 trend.

    Returns a GoldSetup on a fresh, confirmed breakout, else None. Completed
    candles only (start_pos=1). ``broker_symbol`` is the broker's exact gold
    name (some use XAUUSD, XAUUSD.m, GOLD, etc.).
    """
    return evaluate_gold_setup_diagnostic(
        client, broker_symbol, params
    ).setup
