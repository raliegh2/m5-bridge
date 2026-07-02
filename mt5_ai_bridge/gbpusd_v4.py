"""Live MT5 execution adapter for GBPUSD V4.

This module is intended for the m5-bridge repository. It uses completed H4/D1
candles, one GBPUSD position, visible broker-side stops, a 50% partial at 1R,
and ATR trailing for the remainder. Start in READ_ONLY or APPROVAL mode.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .enums import Mode, Signal
from .execution import pip_size, place_market_order
from .sizing import RiskConfig, risk_lot
from .trade_manager import close_position, modify_position_sl

MAGIC = 260704
COMMENT = "GBPUSD V4 Exceptional"


@dataclass(frozen=True)
class LiveParams:
    risk_percent: float = 0.35
    defensive_risk_percent: float = 0.20
    defensive_drawdown_percent: float = 3.0
    pause_drawdown_percent: float = 6.0
    stop_atr: float = 1.5
    target_r: float = 3.0
    partial_r: float = 1.0
    partial_fraction: float = 0.50
    trail_atr: float = 2.5
    max_h4_bars: int = 72
    min_stop_pips: float = 20.0
    max_stop_pips: float = 150.0
    news_minutes_before: int = 10
    news_minutes_after: int = 10


@dataclass(frozen=True)
class Setup:
    side: Signal
    variant: str
    signal_end: datetime
    atr_price: float
    reason: str


def _state_path() -> Path:
    return Path(os.getenv("V4_STATE_PATH", "v4_state.json"))


def _load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {"peak_equity": 0.0, "last_signal_end": None, "positions": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("peak_equity", 0.0)
        payload.setdefault("last_signal_end", None)
        payload.setdefault("positions", {})
        return payload
    except (OSError, ValueError, TypeError):
        return {"peak_equity": 0.0, "last_signal_end": None, "positions": {}}


def _save_state(state: dict) -> None:
    path = _state_path()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(path)


def _rates(client, symbol: str, timeframe: str, count: int) -> Optional[pd.DataFrame]:
    raw = client.copy_rates_from_pos(symbol, timeframe, 1, count)
    if raw is None or len(raw) == 0:
        return None
    frame = pd.DataFrame(raw)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.sort_values("time").reset_index(drop=True)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = frame["high"].diff()
    down_move = -frame["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=frame.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=frame.index,
    )
    atr_series = _atr(frame, period)
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr_series
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / atr_series
    dx = 100 * (plus_di - minus_di).abs() / (
        plus_di + minus_di
    ).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def evaluate_setup(
    client, symbol: str
) -> tuple[Optional[Setup], Optional[pd.DataFrame]]:
    if symbol.upper() != "GBPUSD":
        return None, None
    h4 = _rates(client, symbol, "H4", 180)
    d1 = _rates(client, symbol, "D1", 120)
    if h4 is None or d1 is None or len(h4) < 70 or len(d1) < 55:
        return None, h4

    h4["atr"] = _atr(h4, 14)
    h4["adx"] = _adx(h4, 14)
    h4["avg_volume"] = h4["tick_volume"].rolling(20, min_periods=20).mean()
    h4["volume_ratio"] = h4["tick_volume"] / h4["avg_volume"]
    h4["atr_ratio"] = h4["atr"] / h4["atr"].rolling(20, min_periods=20).mean()
    candle_range = (h4["high"] - h4["low"]).replace(0, np.nan)
    h4["body_ratio"] = (h4["close"] - h4["open"]).abs() / candle_range
    h4["close_location"] = (h4["close"] - h4["low"]) / candle_range

    d1["ema20"] = _ema(d1["close"], 20)
    d1["ema50"] = _ema(d1["close"], 50)
    row = h4.iloc[-1]
    daily = d1.iloc[-1]
    required = [
        row["atr"],
        row["adx"],
        row["volume_ratio"],
        row["atr_ratio"],
        daily["ema20"],
        daily["ema50"],
    ]
    if any(pd.isna(value) for value in required):
        return None, h4

    signal_end = (row["time"] + pd.Timedelta(hours=4)).to_pydatetime()
    long_regime = (
        daily["ema20"] > daily["ema50"] and daily["close"] > daily["ema20"]
    )
    short_regime = (
        daily["ema20"] < daily["ema50"] and daily["close"] < daily["ema20"]
    )

    primary_high = h4["high"].iloc[-21:-1].max()
    primary_low = h4["low"].iloc[-21:-1].min()
    if (
        signal_end.hour == 16
        and row["adx"] >= 18
        and row["volume_ratio"] >= 0.70
        and row["body_ratio"] >= 0.30
        and row["atr_ratio"] >= 1.0
    ):
        if (
            long_regime
            and row["close_location"] >= 0.70
            and row["close"] > primary_high
        ):
            return Setup(
                Signal.BUY,
                "PRIMARY_16UTC_BREAKOUT",
                signal_end,
                float(row["atr"]),
                "D1 uptrend + H4 20-bar volatility-expansion breakout",
            ), h4
        if (
            short_regime
            and row["close_location"] <= 0.30
            and row["close"] < primary_low
        ):
            return Setup(
                Signal.SELL,
                "PRIMARY_16UTC_BREAKOUT",
                signal_end,
                float(row["atr"]),
                "D1 downtrend + H4 20-bar volatility-expansion breakout",
            ), h4

    secondary_high = h4["high"].iloc[-46:-1].max()
    secondary_low = h4["low"].iloc[-46:-1].min()
    if (
        signal_end.hour == 12
        and signal_end.weekday() != 4
        and row["adx"] >= 12
        and row["volume_ratio"] >= 0.70
        and row["body_ratio"] >= 0.50
        and row["atr_ratio"] >= 1.0
    ):
        if (
            long_regime
            and row["close_location"] >= 0.60
            and row["close"] > secondary_high
        ):
            return Setup(
                Signal.BUY,
                "SECONDARY_12UTC_BREAKOUT",
                signal_end,
                float(row["atr"]),
                "D1 uptrend + H4 45-bar strong continuation breakout",
            ), h4
        if (
            short_regime
            and row["close_location"] <= 0.40
            and row["close"] < secondary_low
        ):
            return Setup(
                Signal.SELL,
                "SECONDARY_12UTC_BREAKOUT",
                signal_end,
                float(row["atr"]),
                "D1 downtrend + H4 45-bar strong continuation breakout",
            ), h4
    return None, h4


def _news_blocked(now: datetime, params: LiveParams) -> bool:
    path_text = os.getenv("V4_NEWS_FILE", "").strip()
    if not path_text:
        return False
    path = Path(path_text)
    require_news = os.getenv("V4_REQUIRE_NEWS_FILE", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not path.exists():
        return require_news
    try:
        news = pd.read_csv(path)
        news.columns = [column.lower() for column in news.columns]
        news["time"] = pd.to_datetime(news["time"], utc=True)
        relevant = news[
            news["currency"].astype(str).str.upper().isin(["GBP", "USD"])
            & news["impact"].astype(str).str.lower().eq("high")
        ]
        delta = (relevant["time"] - pd.Timestamp(now)).dt.total_seconds() / 60
        return bool(
            delta.between(
                -params.news_minutes_after,
                params.news_minutes_before,
            ).any()
        )
    except (OSError, ValueError, KeyError):
        return require_news


def _normalize_partial_volume(client, symbol: str, volume: float) -> float:
    info = client.symbol_info(symbol)
    minimum = float(getattr(info, "volume_min", 0.01) or 0.01)
    step = float(getattr(info, "volume_step", 0.01) or 0.01)
    maximum = float(getattr(info, "volume_max", volume) or volume)
    normalized = math.floor(volume / step) * step
    return round(min(max(normalized, minimum), maximum), 2)


def _close_partial(client, position, volume: float) -> tuple[bool, str]:
    tick = client.symbol_info_tick(position.symbol)
    if tick is None:
        return False, "No tick data for partial close"
    if position.type == client.POSITION_TYPE_BUY:
        order_type, price = client.ORDER_TYPE_SELL, tick.bid
    else:
        order_type, price = client.ORDER_TYPE_BUY, tick.ask
    request = {
        "action": client.TRADE_ACTION_DEAL,
        "position": position.ticket,
        "symbol": position.symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": MAGIC,
        "comment": f"{COMMENT} partial",
        "type_time": client.ORDER_TIME_GTC,
    }
    result = client.order_send(request)
    if result is None:
        return False, f"Partial close failed: {client.last_error()}"
    if result.retcode != client.TRADE_RETCODE_DONE:
        return False, f"Partial close rejected: {result.retcode} - {result.comment}"
    return True, f"Closed {volume:.2f} lots on ticket {position.ticket}"


def _effective_risk(
    account, state: dict, params: LiveParams
) -> tuple[float, float, bool]:
    equity = float(account.equity)
    peak = max(float(state.get("peak_equity", 0.0)), equity)
    state["peak_equity"] = peak
    drawdown = (peak - equity) / peak * 100 if peak else 0.0
    if drawdown >= params.pause_drawdown_percent:
        return 0.0, drawdown, True
    if drawdown >= params.defensive_drawdown_percent:
        return params.defensive_risk_percent, drawdown, False
    return params.risk_percent, drawdown, False


def manage_positions(
    client, symbol: str, state: dict, params: LiveParams
) -> list[str]:
    messages: list[str] = []
    positions = [
        p
        for p in (client.positions_get(symbol=symbol) or [])
        if getattr(p, "magic", None) == MAGIC
    ]
    active_tickets = {str(p.ticket) for p in positions}
    state["positions"] = {
        key: value
        for key, value in state.get("positions", {}).items()
        if key in active_tickets
    }
    h4 = _rates(client, symbol, "H4", 250)
    if h4 is None or len(h4) < 20:
        return messages
    h4["atr"] = _atr(h4, 14)
    current_atr = float(h4.iloc[-1]["atr"])

    for position in positions:
        key = str(position.ticket)
        entry = float(position.price_open)
        target = float(getattr(position, "tp", 0.0) or 0.0)
        initial_risk = (
            abs(target - entry) / params.target_r
            if target
            else abs(entry - float(position.sl))
        )
        record = state["positions"].setdefault(
            key,
            {
                "initial_volume": float(position.volume),
                "partial_done": False,
                "initial_risk": initial_risk,
            },
        )
        initial_risk = float(record.get("initial_risk", initial_risk))
        is_buy = position.type == client.POSITION_TYPE_BUY
        favorable = (
            float(position.price_current) - entry
            if is_buy
            else entry - float(position.price_current)
        )

        entered = pd.to_datetime(position.time, unit="s", utc=True)
        since_entry = h4[h4["time"] >= entered]
        if len(since_entry) >= params.max_h4_bars:
            _, message = close_position(client, position.ticket)
            messages.append(message)
            continue

        if (
            favorable >= params.partial_r * initial_risk
            and not record.get("partial_done", False)
        ):
            partial = _normalize_partial_volume(
                client,
                symbol,
                float(record["initial_volume"]) * params.partial_fraction,
            )
            if partial < float(position.volume):
                ok, message = _close_partial(client, position, partial)
                messages.append(message)
                if ok:
                    record["partial_done"] = True
                    _, sl_message = modify_position_sl(client, position, entry)
                    messages.append(sl_message)

        if favorable >= initial_risk and not since_entry.empty and current_atr > 0:
            if is_buy:
                candidate = (
                    float(since_entry["high"].max())
                    - params.trail_atr * current_atr
                )
                current_sl = float(getattr(position, "sl", 0.0) or 0.0)
                if record.get("partial_done"):
                    candidate = max(candidate, entry)
                should_move = candidate < position.price_current and (
                    not current_sl or candidate > current_sl
                )
            else:
                candidate = (
                    float(since_entry["low"].min())
                    + params.trail_atr * current_atr
                )
                current_sl = float(getattr(position, "sl", 0.0) or 0.0)
                if record.get("partial_done"):
                    candidate = min(candidate, entry)
                should_move = candidate > position.price_current and (
                    not current_sl or candidate < current_sl
                )
            if should_move:
                _, message = modify_position_sl(
                    client, position, round(candidate, 5)
                )
                messages.append(message)
    return messages


def run_v4_cycle(
    client,
    journal,
    settings,
    account,
    risk_ok: bool,
    active: bool,
    params: LiveParams = LiveParams(),
) -> dict:
    state = _load_state()
    messages = manage_positions(client, settings.symbol, state, params)
    for message in messages:
        journal.log_order(
            settings.symbol,
            "MANAGE",
            0.0,
            None,
            None,
            None,
            None,
            "UPDATED",
            message,
        )

    effective_risk, drawdown, paused = _effective_risk(account, state, params)
    setup, _ = evaluate_setup(client, settings.symbol)
    thinking = {
        "timeframes": ["D1", "H4"],
        "bias": setup.side.value if setup else "NONE",
        "aligned": bool(setup),
        "setup_valid": bool(setup),
        "note": (
            setup.reason
            if setup
            else "Waiting for a validated V4 completed-H4 breakout."
        ),
        "engines": [
            {
                "name": "GBPUSD V4",
                "ready": bool(setup),
                "bias": setup.side.value if setup else "NONE",
                "confidence": 1.0 if setup else 0.0,
                "reason": (
                    setup.reason
                    if setup
                    else f"No setup. Strategy drawdown={drawdown:.2f}%."
                ),
            }
        ],
    }

    if setup is None:
        _save_state(state)
        return thinking
    journal.log_signal(
        settings.symbol,
        setup.side.value,
        setup.reason,
        {"time": setup.signal_end.isoformat(), "atr": setup.atr_price},
        setup=1,
        filtered=0,
    )

    if settings.mode is Mode.READ_ONLY or not risk_ok or not active or paused:
        _save_state(state)
        return thinking
    if client.positions_get(symbol=settings.symbol) or []:
        _save_state(state)
        return thinking
    if _news_blocked(datetime.now(timezone.utc), params):
        thinking["note"] = "Entry blocked by GBP/USD high-impact-news window."
        _save_state(state)
        return thinking

    marker = setup.signal_end.isoformat()
    if state.get("last_signal_end") == marker:
        _save_state(state)
        return thinking

    pip = pip_size(client, settings.symbol) or 0.0001
    stop_pips = min(
        max(
            params.stop_atr * setup.atr_price / pip,
            params.min_stop_pips,
        ),
        params.max_stop_pips,
    )
    target_pips = params.target_r * stop_pips
    risk_cfg = RiskConfig(
        enabled=True,
        risk_percent=min(effective_risk, float(settings.risk_percent), 0.50),
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
        comment=f"{COMMENT} {setup.variant}",
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
        message,
    )
    if ok:
        state["last_signal_end"] = marker
    _save_state(state)
    return thinking
