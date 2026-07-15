"""Live GBPUSD/GBPJPY provider for the recovered V14.3 ICT rules.

The provider reads completed MT5 M1 candles only, recreates the original
sweep/reclaim and breakout-fade candidate families, applies the locked V14.3
selection filters, and returns dictionaries accepted by
``mt5_ai_bridge.v14_3_live_signals.load_legacy_gbp_ict_signals``.

No historical outcome calculation is used here. The research-only
``_future_outcome`` logic is intentionally excluded to prevent lookahead.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from mt5_ai_bridge.v14_3_live_execution import pip_size, resolve_broker_symbol

SYMBOLS = ("GBPUSD", "GBPJPY")
WINDOWS_MINUTES = (15, 30, 60)
SETUP_PRIORITY = {
    "sweep_reclaim_60": 0.0,
    "sweep_reclaim_30": 1.0,
    "sweep_reclaim_15": 2.0,
    "breakout_60_fade": 3.2,
    "breakout_30_fade": 4.2,
    "breakout_15_fade": 5.2,
}


@dataclass(frozen=True)
class ProviderConfig:
    candle_count: int = 480
    atr_window: int = 14
    minimum_gap_minutes: int = 60
    lookback_minutes: int = 90
    requested_risk_percent: float = 0.45
    target_r: float = 1.25
    atr_stop_multiplier: float = 0.35
    sweep_buffer_atr: float = 0.02
    minimum_stop_pips_fx: float = 5.0
    minimum_stop_pips_jpy: float = 7.5
    minimum_break_pips_fx: float = 0.5
    minimum_break_pips_jpy: float = 0.75

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        return cls(
            candle_count=max(180, int(os.getenv("V14_3_GBP_ICT_M1_BARS", "480"))),
            lookback_minutes=max(
                1,
                min(180, int(os.getenv("V14_3_GBP_ICT_LOOKBACK_MINUTES", "90"))),
            ),
        )


def _frame(rates: Any) -> pd.DataFrame:
    frame = pd.DataFrame(rates)
    if frame.empty:
        return frame
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"M1 rates missing columns: {sorted(missing)}")
    frame = frame.copy()
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True, errors="coerce")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame[["time", "open", "high", "low", "close"]]
        .dropna()
        .sort_values("time")
        .drop_duplicates("time")
        .reset_index(drop=True)
    )


def _add_atr(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    output = frame.copy()
    previous_close = output["close"].shift(1)
    true_range = pd.concat(
        [
            output["high"] - output["low"],
            (output["high"] - previous_close).abs(),
            (output["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    output["atr"] = true_range.rolling(window, min_periods=window).mean()
    return output


def generate_raw_candidates(
    symbol: str,
    candles: pd.DataFrame,
    config: ProviderConfig,
) -> pd.DataFrame:
    """Create raw candidates using completed candles and no future bars."""
    symbol = symbol.upper()
    frame = _add_atr(candles, config.atr_window)
    if frame.empty:
        return pd.DataFrame()

    pip = 0.01 if symbol.endswith("JPY") else 0.0001
    minimum_break = (
        config.minimum_break_pips_jpy
        if symbol.endswith("JPY")
        else config.minimum_break_pips_fx
    ) * pip
    rows: list[dict[str, Any]] = []

    for minutes in WINDOWS_MINUTES:
        reference_high = frame["high"].rolling(minutes, min_periods=minutes).max().shift(1)
        reference_low = frame["low"].rolling(minutes, min_periods=minutes).min().shift(1)
        previous_close = frame["close"].shift(1)
        previous_reference_high = reference_high.shift(1)
        previous_reference_low = reference_low.shift(1)

        conditions = (
            (
                "sweep_reclaim",
                -1,
                (frame["high"] > reference_high + minimum_break)
                & (frame["close"] < reference_high),
            ),
            (
                "sweep_reclaim",
                1,
                (frame["low"] < reference_low - minimum_break)
                & (frame["close"] > reference_low),
            ),
            (
                "breakout_fade",
                -1,
                (previous_close > previous_reference_high + minimum_break)
                & (frame["close"] < reference_high)
                & (frame["high"] >= reference_high),
            ),
            (
                "breakout_fade",
                1,
                (previous_close < previous_reference_low - minimum_break)
                & (frame["close"] > reference_low)
                & (frame["low"] <= reference_low),
            ),
        )

        for family, direction, condition in conditions:
            setup = (
                f"sweep_reclaim_{minutes}"
                if family == "sweep_reclaim"
                else f"breakout_{minutes}_fade"
            )
            for index in np.flatnonzero(condition.fillna(False).to_numpy()):
                candle = frame.iloc[int(index)]
                atr = float(candle["atr"])
                if not np.isfinite(atr) or atr <= 0:
                    continue
                rows.append(
                    {
                        "entry_time": pd.Timestamp(candle["time"]),
                        "symbol": symbol,
                        "setup": setup,
                        "direction": int(direction),
                        "priority": float(SETUP_PRIORITY[setup]),
                        "candle_high": float(candle["high"]),
                        "candle_low": float(candle["low"]),
                        "signal_atr": atr,
                    }
                )

    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .drop_duplicates(["entry_time", "direction", "symbol", "setup"])
        .sort_values(["symbol", "entry_time", "priority", "setup"])
        .reset_index(drop=True)
    )


def deduplicate_gap_stream(
    candidates: pd.DataFrame,
    minimum_gap_minutes: int = 60,
) -> pd.DataFrame:
    """Keep the first highest-priority signal per symbol after each gap."""
    if candidates.empty:
        return candidates.copy()
    ordered = candidates.sort_values(
        ["symbol", "entry_time", "priority", "setup"]
    ).reset_index(drop=True)
    minimum_gap = pd.Timedelta(minutes=minimum_gap_minutes)
    last_by_symbol: dict[str, pd.Timestamp] = {}
    rows: list[dict[str, Any]] = []
    for row in ordered.to_dict("records"):
        symbol = str(row["symbol"])
        entry_time = pd.Timestamp(row["entry_time"])
        previous = last_by_symbol.get(symbol)
        if previous is None or entry_time - previous >= minimum_gap:
            rows.append(row)
            last_by_symbol[symbol] = entry_time
    return pd.DataFrame(rows).sort_values(["entry_time", "symbol", "setup"]).reset_index(drop=True)


def apply_locked_filters(candidates: pd.DataFrame) -> pd.DataFrame:
    """Apply the frozen V14.3 setup, weekday, and UTC-hour filters."""
    if candidates.empty:
        return candidates.copy()
    frame = candidates.copy()
    times = pd.to_datetime(frame["entry_time"], utc=True)
    gbpjpy_breakout_fade = (
        frame["symbol"].eq("GBPJPY")
        & frame["setup"].isin(
            ["breakout_15_fade", "breakout_30_fade", "breakout_60_fade"]
        )
    )
    gbpusd_sweep_15 = frame["symbol"].eq("GBPUSD") & frame["setup"].eq(
        "sweep_reclaim_15"
    )
    tuesday = times.dt.dayofweek.eq(1)
    blocked_hour = times.dt.hour.isin([7, 13])
    return (
        frame.loc[~(gbpjpy_breakout_fade | gbpusd_sweep_15 | tuesday | blocked_hour)]
        .sort_values(["entry_time", "symbol", "setup"])
        .reset_index(drop=True)
    )


def _live_payload(
    client: Any,
    broker_symbol: str,
    row: Any,
    config: ProviderConfig,
) -> dict[str, Any] | None:
    symbol = str(row.symbol).upper()
    info = client.symbol_info(broker_symbol)
    tick = client.symbol_info_tick(broker_symbol)
    if info is None or tick is None:
        return None

    pip = pip_size(info, symbol)
    if pip <= 0:
        return None
    minimum_stop = (
        config.minimum_stop_pips_jpy
        if symbol.endswith("JPY")
        else config.minimum_stop_pips_fx
    ) * pip
    atr = float(row.signal_atr)
    buffer = max(atr * config.sweep_buffer_atr, 0.1 * pip)
    direction = int(row.direction)

    if direction > 0:
        entry = float(tick.ask)
        structure_stop = float(row.candle_low) - buffer
        stop_distance = max(
            entry - structure_stop,
            minimum_stop,
            atr * config.atr_stop_multiplier,
        )
        side = "BUY"
    else:
        entry = float(tick.bid)
        structure_stop = float(row.candle_high) + buffer
        stop_distance = max(
            structure_stop - entry,
            minimum_stop,
            atr * config.atr_stop_multiplier,
        )
        side = "SELL"

    if not np.isfinite(stop_distance) or stop_distance <= 0:
        return None
    stop_pips = stop_distance / pip
    setup = str(row.setup)
    window = int(
        setup.split("_")[-1]
        if setup.startswith("sweep")
        else setup.split("_")[1]
    )
    return {
        "symbol": symbol,
        "engine": f"ICT_V14_3_{symbol}",
        "setup": setup,
        "side": side,
        "signal_time": pd.Timestamp(row.entry_time).to_pydatetime(),
        "risk_percent": config.requested_risk_percent,
        "stop_pips": float(stop_pips),
        "target_pips": float(stop_pips * config.target_r),
        "metadata": {
            "source": "v14_3_live_m1",
            "timeframe": "M1",
            "reference_window_minutes": window,
            "target_r": config.target_r,
            "signal_atr": atr,
            "structure_stop": structure_stop,
            "completed_candle_only": True,
        },
    }


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def build_live_signals(client: Any) -> list[dict[str, Any]]:
    """Return recent, filtered GBP V14.3 ICT signals from completed M1 bars."""
    config = ProviderConfig.from_env()
    now = _utc_now()
    cutoff = now - pd.Timedelta(minutes=config.lookback_minutes)
    outputs: list[dict[str, Any]] = []

    for symbol in SYMBOLS:
        broker_symbol = resolve_broker_symbol(client, symbol)
        # start=1 excludes the currently forming M1 candle.
        rates = client.copy_rates_from_pos(
            broker_symbol,
            "M1",
            1,
            config.candle_count,
        )
        candles = _frame(rates)
        if len(candles) < max(WINDOWS_MINUTES) + config.atr_window + 2:
            continue
        raw = generate_raw_candidates(symbol, candles, config)
        selected = apply_locked_filters(
            deduplicate_gap_stream(raw, config.minimum_gap_minutes)
        )
        if selected.empty:
            continue
        recent = selected[pd.to_datetime(selected["entry_time"], utc=True) >= cutoff]
        for row in recent.itertuples(index=False):
            payload = _live_payload(client, broker_symbol, row, config)
            if payload is not None:
                outputs.append(payload)

    unique = {
        (
            item["symbol"],
            item["engine"],
            item["setup"],
            item["side"],
            pd.Timestamp(item["signal_time"]).isoformat(),
        ): item
        for item in outputs
    }
    return sorted(unique.values(), key=lambda item: pd.Timestamp(item["signal_time"]))
