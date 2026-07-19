"""V14.19 shadow-only range mean-reversion engine.

This module is deliberately isolated from broker transmission and portfolio
allocation. It generates completed-candle D1 range-reversion candidates from
historical bid/ask data, enters only on a later H4 bar, and records shadow
outcomes with zero requested or executed risk.

No function in this module sends an order or increases V14.18 risk.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

CORE_SYMBOLS = ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")
ENTRY_HOURS_UTC = (8, 12, 16)


@dataclass(frozen=True)
class RangeShadowSpec:
    name: str = "V14_19_D1_RANGE_REVERSION_SHADOW"
    adx_maximum: float = 25.0
    z_entry: float = 1.25
    maximum_ema_gap_atr: float = 0.80
    maximum_range_width_atr: float = 12.0
    reclaim_fraction: float = 0.25
    stop_atr: float = 2.20
    target_r: float = 1.60
    maximum_holding_h4_bars: int = 15
    slippage_reserve_r: float = 0.025


SPEC = RangeShadowSpec()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
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
        alpha=1.0 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high_diff = frame["high"].diff()
    low_diff = -frame["low"].diff()
    plus_dm = pd.Series(
        np.where(
            (high_diff > low_diff) & (high_diff > 0),
            high_diff,
            0.0,
        ),
        index=frame.index,
    )
    minus_dm = pd.Series(
        np.where(
            (low_diff > high_diff) & (low_diff > 0),
            low_diff,
            0.0,
        ),
        index=frame.index,
    )
    true_range = atr(frame, period)
    plus_di = (
        100.0
        * plus_dm.ewm(
            alpha=1.0 / period,
            adjust=False,
            min_periods=period,
        ).mean()
        / true_range.replace(0, np.nan)
    )
    minus_di = (
        100.0
        * minus_dm.ewm(
            alpha=1.0 / period,
            adjust=False,
            min_periods=period,
        ).mean()
        / true_range.replace(0, np.nan)
    )
    dx = (
        100.0
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )
    return dx.ewm(
        alpha=1.0 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def resample_ohlc(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    work = frame.copy()
    work["time"] = pd.to_datetime(work["time"], utc=True)
    indexed = work.set_index("time")
    output = indexed.resample(
        rule,
        label="left",
        closed="left",
    ).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
    )
    return output.dropna(
        subset=["open", "high", "low", "close"],
    ).reset_index()


def signal_available_at(day_start: Any) -> pd.Timestamp:
    """Return when the completed D1 signal becomes observable."""
    return pd.Timestamp(day_start).tz_convert("UTC") + pd.Timedelta(days=1)


def first_permitted_entry_time(
    available_at: Any,
    h4_times: pd.Series,
) -> pd.Timestamp | None:
    """Return the first later H4 entry in an allowed UTC session."""
    available = pd.Timestamp(available_at)
    candidates = pd.to_datetime(h4_times, utc=True)
    mask = (
        (candidates >= available)
        & candidates.dt.hour.isin(ENTRY_HOURS_UTC)
        & (candidates <= available + pd.Timedelta(days=3))
    )
    if not bool(mask.any()):
        return None
    return pd.Timestamp(candidates.loc[mask].iloc[0])


def resolve_bar_exit(
    *,
    side: str,
    high: float,
    low: float,
    stop: float,
    target: float,
) -> str | None:
    """Resolve one bar conservatively: stop wins when both levels trade."""
    side = str(side).upper()
    if side == "BUY":
        stop_hit = float(low) <= float(stop)
        target_hit = float(high) >= float(target)
    elif side == "SELL":
        stop_hit = float(high) >= float(stop)
        target_hit = float(low) <= float(target)
    else:
        raise ValueError(f"Unsupported side: {side}")
    if stop_hit:
        return "STOP"
    if target_hit:
        return "TARGET"
    return None


def _daily_signal_frame(h1_bid: pd.DataFrame) -> pd.DataFrame:
    daily = resample_ohlc(h1_bid, "1D").sort_values("time").reset_index(drop=True)
    daily["atr14"] = atr(daily)
    daily["adx14"] = adx(daily)
    daily["ema20"] = ema(daily["close"], 20)
    daily["ema50"] = ema(daily["close"], 50)
    mean20 = daily["close"].rolling(20, min_periods=20).mean()
    std20 = daily["close"].rolling(20, min_periods=20).std().replace(0, np.nan)
    daily["z20"] = (daily["close"] - mean20) / std20
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
    candle_range = (daily["high"] - daily["low"]).replace(0, np.nan)
    daily["close_location"] = (daily["close"] - daily["low"]) / candle_range
    daily["available_at"] = daily["time"].map(signal_available_at)

    quiet = (
        (daily["adx14"] <= SPEC.adx_maximum)
        & (daily["ema_gap_atr"] <= SPEC.maximum_ema_gap_atr)
        & (daily["range_width_atr"] <= SPEC.maximum_range_width_atr)
    )
    daily["buy_signal"] = (
        quiet
        & (daily["z20"] <= -SPEC.z_entry)
        & (daily["close_location"] >= SPEC.reclaim_fraction)
    )
    daily["sell_signal"] = (
        quiet
        & (daily["z20"] >= SPEC.z_entry)
        & (daily["close_location"] <= 1.0 - SPEC.reclaim_fraction)
    )
    return daily


def _execution_frame(
    h1_bid: pd.DataFrame,
    h1_ask: pd.DataFrame,
) -> pd.DataFrame:
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
    return (
        bid.merge(ask, on="time", how="inner")
        .sort_values("time")
        .drop_duplicates("time")
        .reset_index(drop=True)
    )


def _simulate_trade(
    *,
    symbol: str,
    side: str,
    entry_index: int,
    frame: pd.DataFrame,
    signal: pd.Series,
) -> dict[str, Any]:
    entry_bar = frame.iloc[entry_index]
    side = str(side).upper()
    entry = float(entry_bar["ask_open"]) if side == "BUY" else float(entry_bar["bid_open"])
    signal_atr = float(signal["atr14"])
    risk_distance = signal_atr * SPEC.stop_atr
    if not np.isfinite(risk_distance) or risk_distance <= 0:
        raise ValueError("Invalid signal ATR")

    if side == "BUY":
        stop = entry - risk_distance
        target = entry + SPEC.target_r * risk_distance
    else:
        stop = entry + risk_distance
        target = entry - SPEC.target_r * risk_distance

    final_index = min(len(frame) - 1, entry_index + SPEC.maximum_holding_h4_bars - 1)
    exit_price: float | None = None
    exit_reason = "TIME"
    exit_index = final_index

    for index in range(entry_index, final_index + 1):
        bar = frame.iloc[index]
        if side == "BUY":
            outcome = resolve_bar_exit(
                side=side,
                high=float(bar["bid_high"]),
                low=float(bar["bid_low"]),
                stop=stop,
                target=target,
            )
        else:
            outcome = resolve_bar_exit(
                side=side,
                high=float(bar["ask_high"]),
                low=float(bar["ask_low"]),
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
        exit_price = float(last["bid_close"]) if side == "BUY" else float(last["ask_close"])

    gross_r = (
        (exit_price - entry) / risk_distance
        if side == "BUY"
        else (entry - exit_price) / risk_distance
    )
    base_net_r = gross_r - SPEC.slippage_reserve_r
    embedded_spread_r = float(entry_bar["ask_open"] - entry_bar["bid_open"]) / risk_distance

    return {
        "engine": SPEC.name,
        "family": "D1_RANGE_MEAN_REVERSION",
        "mode": "RANGE_SHADOW",
        "symbol": symbol,
        "side": side,
        "signal_time": pd.Timestamp(signal["time"]),
        "signal_available_at": pd.Timestamp(signal["available_at"]),
        "entry_time": pd.Timestamp(entry_bar["time"]),
        "exit_time": pd.Timestamp(frame.iloc[exit_index]["time"]) + pd.Timedelta(hours=4),
        "entry_price": entry,
        "exit_price": exit_price,
        "stop_price": stop,
        "target_price": target,
        "signal_atr": signal_atr,
        "signal_adx": float(signal["adx14"]),
        "signal_z20": float(signal["z20"]),
        "signal_ema_gap_atr": float(signal["ema_gap_atr"]),
        "signal_range_width_atr": float(signal["range_width_atr"]),
        "signal_close_location": float(signal["close_location"]),
        "gross_r_multiple": float(gross_r),
        "embedded_spread_r": float(embedded_spread_r),
        "base_slippage_reserve_r": SPEC.slippage_reserve_r,
        "base_net_r_multiple": float(base_net_r),
        "exit_reason": exit_reason,
        "holding_h4_bars": int(exit_index - entry_index + 1),
        "shadow_only": True,
        "requested_risk_percent": 0.0,
        "executed_risk_percent": 0.0,
        "transmitted": False,
        "promotion_status": "SHADOW_ONLY",
    }


def generate_shadow_trades(
    symbol: str,
    h1_bid: pd.DataFrame,
    h1_ask: pd.DataFrame,
) -> pd.DataFrame:
    """Generate independent shadow trades with one open trade per symbol."""
    symbol = str(symbol).upper()
    if symbol not in CORE_SYMBOLS:
        raise ValueError(f"Unsupported V14.19 core symbol: {symbol}")

    daily = _daily_signal_frame(h1_bid)
    execution = _execution_frame(h1_bid, h1_ask)
    if execution.empty:
        return pd.DataFrame()

    time_to_index = {
        pd.Timestamp(value): index
        for index, value in enumerate(pd.to_datetime(execution["time"], utc=True))
    }
    records: list[dict[str, Any]] = []
    next_free_time = pd.Timestamp.min.tz_localize("UTC")

    signals: list[tuple[pd.Timestamp, str, pd.Series]] = []
    for _, row in daily.loc[daily["buy_signal"]].iterrows():
        signals.append((pd.Timestamp(row["available_at"]), "BUY", row))
    for _, row in daily.loc[daily["sell_signal"]].iterrows():
        signals.append((pd.Timestamp(row["available_at"]), "SELL", row))
    signals.sort(key=lambda item: (item[0], 0 if item[1] == "BUY" else 1))

    for available_at, side, signal in signals:
        entry_time = first_permitted_entry_time(available_at, execution["time"])
        if entry_time is None or entry_time < next_free_time:
            continue
        entry_index = time_to_index.get(entry_time)
        if entry_index is None:
            continue
        trade = _simulate_trade(
            symbol=symbol,
            side=side,
            entry_index=entry_index,
            frame=execution,
            signal=signal,
        )
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


def shadow_configuration() -> dict[str, Any]:
    return {
        **asdict(SPEC),
        "core_symbols": list(CORE_SYMBOLS),
        "entry_hours_utc": list(ENTRY_HOURS_UTC),
        "shadow_only": True,
        "requested_risk_percent": 0.0,
        "executed_risk_percent": 0.0,
        "broker_transmission": False,
    }


__all__ = [
    "CORE_SYMBOLS",
    "ENTRY_HOURS_UTC",
    "RangeShadowSpec",
    "SPEC",
    "adx",
    "apply_scenario_reserve",
    "atr",
    "first_permitted_entry_time",
    "generate_shadow_trades",
    "resolve_bar_exit",
    "resample_ohlc",
    "shadow_configuration",
    "signal_available_at",
]
