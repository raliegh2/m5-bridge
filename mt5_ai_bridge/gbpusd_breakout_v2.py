"""Validated GBPUSD H4 breakout swing engine.

The engine is intentionally narrow: one GBPUSD position, one setup family, and
completed candles only.  It replaces the losing mixed pullback/range stack with
the most stable proxy-tested variant.

Proxy-tested defaults (2016-01-04 through 2026-07-01):
- H4 55-bar Donchian breakout
- D1 EMA20/EMA50 regime alignment
- H4 ADX >= 15
- H4 tick volume >= 0.8 x its 20-bar average
- entries only at completed H4 closes ending 12:00 or 16:00 UTC
- 2.0 x H4 ATR hard stop, clipped to 20-150 pips
- 2R take profit
- trail activates at 1R, 2.5 x H4 ATR behind completed-bar extremes
- maximum 90 completed H4 bars in a trade
- no partial profit taking (preserves positive payoff skew)

This module does not bypass the shared account risk limits in app.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .enums import Mode, Signal
from .execution import pip_size, place_market_order
from .sizing import RiskConfig, risk_lot
from .trade_manager import close_position, modify_position_sl

MAGIC = 260755
COMMENT = "GBPUSD Breakout V2"
_LAST_SIGNAL_END: dict[str, str] = {}


@dataclass(frozen=True)
class BreakoutParams:
    channel_bars: int = 55
    adx_min: float = 15.0
    volume_ratio_min: float = 0.80
    stop_atr: float = 2.0
    target_r: float = 2.0
    trail_atr: float = 2.5
    trail_start_r: float = 1.0
    min_stop_pips: float = 20.0
    max_stop_pips: float = 150.0
    max_hold_h4_bars: int = 90
    entry_end_hours_utc: tuple[int, ...] = (12, 16)


@dataclass(frozen=True)
class BreakoutSetup:
    side: Signal
    signal_end: datetime
    atr_price: float
    reason: str


def _rates(client, symbol: str, timeframe: str, bars: int) -> Optional[pd.DataFrame]:
    """Return completed candles only (MT5 start_pos=1)."""
    raw = client.copy_rates_from_pos(symbol, timeframe, 1, bars)
    if raw is None or len(raw) == 0:
        return None
    frame = pd.DataFrame(raw)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.sort_values("time").reset_index(drop=True)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = frame["high"].diff()
    down_move = -frame["low"].diff()
    plus_dm = pd.Series(
        ((up_move > down_move) & (up_move > 0)).astype(float) * up_move.clip(lower=0),
        index=frame.index,
    )
    minus_dm = pd.Series(
        ((down_move > up_move) & (down_move > 0)).astype(float) * down_move.clip(lower=0),
        index=frame.index,
    )
    atr_series = _atr(frame, period)
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr_series
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr_series
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def evaluate_setup(
    client,
    symbol: str,
    params: BreakoutParams = BreakoutParams(),
) -> tuple[Optional[BreakoutSetup], Optional[pd.DataFrame]]:
    """Evaluate the latest completed H4 candle against completed D1 context."""
    if symbol.upper() != "GBPUSD":
        return None, None

    h4 = _rates(client, symbol, "H4", max(160, params.channel_bars + 60))
    d1 = _rates(client, symbol, "D1", 120)
    if h4 is None or d1 is None or len(h4) <= params.channel_bars:
        return None, h4

    h4["atr"] = _atr(h4, 14)
    h4["adx"] = _adx(h4, 14)
    h4["avg_tick_volume"] = h4["tick_volume"].rolling(20, min_periods=20).mean()
    d1["ema20"] = _ema(d1["close"], 20)
    d1["ema50"] = _ema(d1["close"], 50)

    latest = h4.iloc[-1]
    daily = d1.iloc[-1]
    required = (
        latest["atr"], latest["adx"], latest["avg_tick_volume"],
        daily["ema20"], daily["ema50"],
    )
    if any(pd.isna(value) for value in required):
        return None, h4

    signal_end = latest["time"].to_pydatetime() + pd.Timedelta(hours=4)
    if signal_end.hour not in params.entry_end_hours_utc:
        return None, h4

    prior = h4.iloc[-(params.channel_bars + 1):-1]
    channel_high = float(prior["high"].max())
    channel_low = float(prior["low"].min())
    volume_ratio = float(latest["tick_volume"] / latest["avg_tick_volume"])

    long_regime = daily["ema20"] > daily["ema50"] and daily["close"] > daily["ema20"]
    short_regime = daily["ema20"] < daily["ema50"] and daily["close"] < daily["ema20"]
    common = float(latest["adx"]) >= params.adx_min and volume_ratio >= params.volume_ratio_min

    if common and long_regime and latest["close"] > channel_high:
        return BreakoutSetup(
            Signal.BUY,
            signal_end,
            float(latest["atr"]),
            f"H4 close broke 55-bar high; D1 EMA20>EMA50; ADX={latest['adx']:.1f}; volume={volume_ratio:.2f}x",
        ), h4

    if common and short_regime and latest["close"] < channel_low:
        return BreakoutSetup(
            Signal.SELL,
            signal_end,
            float(latest["atr"]),
            f"H4 close broke 55-bar low; D1 EMA20<EMA50; ADX={latest['adx']:.1f}; volume={volume_ratio:.2f}x",
        ), h4

    return None, h4


def _initial_risk_price(position) -> Optional[float]:
    entry = float(getattr(position, "price_open", 0.0) or 0.0)
    tp = float(getattr(position, "tp", 0.0) or 0.0)
    if entry <= 0:
        return None
    # The engine always places a 2R target. TP therefore preserves initial risk
    # after the stop has been trailed.
    if tp > 0:
        return abs(tp - entry) / 2.0
    sl = float(getattr(position, "sl", 0.0) or 0.0)
    return abs(entry - sl) if sl > 0 else None


def manage_positions(
    client,
    symbol: str,
    params: BreakoutParams = BreakoutParams(),
) -> list[str]:
    """Apply H4 ATR trailing and the 90-bar time exit to engine positions."""
    messages: list[str] = []
    positions = [
        p for p in (client.positions_get(symbol=symbol) or [])
        if getattr(p, "magic", None) == MAGIC
    ]
    if not positions:
        return messages

    h4 = _rates(client, symbol, "H4", 250)
    if h4 is None or len(h4) < 20:
        return messages
    h4["atr"] = _atr(h4, 14)
    current_atr = float(h4.iloc[-1]["atr"])
    if pd.isna(current_atr) or current_atr <= 0:
        return messages

    for position in positions:
        entered = pd.to_datetime(getattr(position, "time", 0), unit="s", utc=True)
        since_entry = h4[h4["time"] >= entered]
        if len(since_entry) >= params.max_hold_h4_bars:
            ok, message = close_position(client, position.ticket)
            messages.append(message)
            continue

        risk_price = _initial_risk_price(position)
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        current = float(getattr(position, "price_current", 0.0) or 0.0)
        if not risk_price or entry <= 0 or current <= 0 or since_entry.empty:
            continue

        is_buy = position.type == client.POSITION_TYPE_BUY
        favourable = current - entry if is_buy else entry - current
        if favourable < params.trail_start_r * risk_price:
            continue

        if is_buy:
            candidate = float(since_entry["high"].max()) - params.trail_atr * current_atr
            if candidate >= current:
                continue
            current_sl = float(getattr(position, "sl", 0.0) or 0.0)
            if current_sl and candidate <= current_sl:
                continue
        else:
            candidate = float(since_entry["low"].min()) + params.trail_atr * current_atr
            if candidate <= current:
                continue
            current_sl = float(getattr(position, "sl", 0.0) or 0.0)
            if current_sl and candidate >= current_sl:
                continue

        ok, message = modify_position_sl(client, position, round(candidate, 5))
        messages.append(message)

    return messages


def run_breakout_cycle(
    client,
    journal,
    settings,
    account,
    risk_ok: bool,
    active: bool,
    params: BreakoutParams = BreakoutParams(),
) -> dict:
    """Manage existing exposure and optionally open one validated setup."""
    for message in manage_positions(client, settings.symbol, params):
        journal.log_order(
            settings.symbol, "MANAGE", 0.0, None, None, None, None,
            "UPDATED", message,
        )

    setup, _ = evaluate_setup(client, settings.symbol, params)
    thinking = {
        "timeframes": [],
        "bias": setup.side.value if setup else "NONE",
        "aligned": bool(setup),
        "setup_valid": bool(setup),
        "note": setup.reason if setup else "Waiting for a completed H4 55-bar breakout with D1 regime, ADX and volume confirmation.",
        "engines": [{
            "name": "GBPUSD Breakout V2",
            "ready": bool(setup),
            "bias": setup.side.value if setup else "NONE",
            "confidence": 1.0 if setup else 0.0,
            "reason": setup.reason if setup else "No validated breakout on the latest completed H4 candle.",
        }],
    }

    if setup is None:
        return thinking

    journal.log_signal(
        settings.symbol,
        setup.side.value,
        setup.reason,
        {"time": setup.signal_end.isoformat(), "atr": setup.atr_price},
        setup=1,
        filtered=0,
    )

    if settings.mode is Mode.READ_ONLY or not risk_ok or not active:
        return thinking
    if client.positions_get(symbol=settings.symbol) or []:
        return thinking

    marker = setup.signal_end.isoformat()
    if _LAST_SIGNAL_END.get(settings.symbol) == marker:
        return thinking

    pip = pip_size(client, settings.symbol) or 0.0001
    stop_pips = min(
        max((params.stop_atr * setup.atr_price) / pip, params.min_stop_pips),
        params.max_stop_pips,
    )
    target_pips = params.target_r * stop_pips
    risk_cfg = RiskConfig(
        enabled=True,
        risk_percent=min(float(settings.risk_percent), 1.0),
        pip_value_per_lot=float(settings.pip_value_per_lot),
        max_lot=float(settings.max_lot),
    )
    volume = risk_lot(float(account.balance), stop_pips, risk_cfg)
    ok, message = place_market_order(
        client,
        settings.symbol,
        setup.side,
        volume,
        stop_pips,
        target_pips,
        magic=MAGIC,
        comment=COMMENT,
    )
    journal.log_order(
        settings.symbol,
        setup.side.value,
        volume,
        None,
        stop_pips,
        target_pips,
        None,
        "FILLED" if ok else "REJECTED",
        f"[{COMMENT}] {message}",
    )
    if ok:
        _LAST_SIGNAL_END[settings.symbol] = marker
    return thinking
