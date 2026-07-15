"""Research/shadow ICT-style signal engines for the three non-GBP symbols.

The module is deterministic and has no broker/order API. Signals are formed only
from completed H1, H4 and D1 candles. A session range must finish before a later
sweep/reclaim candle can become a candidate, preventing future-data leakage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np
import pandas as pd


ENGINE_BY_SYMBOL = {
    "EURUSD": "EURUSD_ICT_LIQUIDITY",
    "AUDUSD": "AUDUSD_ICT_ASIA_LONDON",
    "USDJPY": "USDJPY_ICT_SESSION_SWEEP",
}
SETUP_BY_SYMBOL = {
    "EURUSD": "eurusd_ict_liquidity",
    "AUDUSD": "audusd_ict_asia_london",
    "USDJPY": "usdjpy_ict_session_sweep",
}


@dataclass(frozen=True)
class IctProfile:
    name: str
    session_start_hour: int
    session_end_hour: int
    entry_start_hour: int
    entry_end_hour: int
    displacement_atr: float
    sweep_atr: float
    stop_buffer_atr: float
    target_r: float
    max_holding_hours: int
    require_daily_bias: bool = True
    require_h4_bias: bool = True
    allowed_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)


PROFILES: dict[str, tuple[IctProfile, ...]] = {
    "EURUSD": (
        IctProfile("eu_london_15", 0, 6, 7, 12, 0.25, 0.00, 0.10, 1.5, 24),
        IctProfile("eu_london_20", 0, 6, 7, 12, 0.25, 0.00, 0.10, 2.0, 30),
        IctProfile("eu_london_25", 0, 6, 7, 12, 0.35, 0.00, 0.15, 2.5, 36),
        IctProfile("eu_ny_20", 7, 11, 12, 17, 0.25, 0.00, 0.10, 2.0, 30),
        IctProfile("eu_ny_relaxed", 7, 11, 12, 17, 0.20, 0.00, 0.15, 1.5, 24, True, False),
    ),
    "AUDUSD": (
        IctProfile("au_london_15", 0, 5, 6, 11, 0.20, 0.00, 0.10, 1.5, 24, True, True),
        IctProfile("au_london_20", 0, 5, 6, 11, 0.25, 0.00, 0.10, 2.0, 30, True, True),
        IctProfile("au_london_relaxed", 0, 6, 7, 12, 0.20, 0.00, 0.15, 1.5, 24, True, False),
        IctProfile("au_ny_20", 0, 6, 12, 17, 0.25, 0.00, 0.10, 2.0, 30, True, True),
    ),
    "USDJPY": (
        IctProfile("uj_london_15", 0, 6, 7, 12, 0.20, 0.00, 0.10, 1.5, 24, True, True),
        IctProfile("uj_london_20", 0, 6, 7, 12, 0.25, 0.00, 0.10, 2.0, 30, True, True),
        IctProfile("uj_ny_20", 0, 7, 12, 17, 0.25, 0.00, 0.10, 2.0, 30, True, True),
        IctProfile("uj_ny_relaxed", 0, 7, 12, 18, 0.20, 0.00, 0.15, 1.5, 24, False, True),
        IctProfile("uj_london_25", 0, 6, 7, 13, 0.35, 0.00, 0.15, 2.5, 36, True, True),
    ),
}


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
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def prepare_frames(
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    d1: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    h1 = h1.copy().sort_values("time").reset_index(drop=True)
    h4 = h4.copy().sort_values("time").reset_index(drop=True)
    d1 = d1.copy().sort_values("time").reset_index(drop=True)

    h1["atr14"] = atr(h1)
    h1["ema20"] = ema(h1["close"], 20)
    h1["ema50"] = ema(h1["close"], 50)
    h1["range"] = h1["high"] - h1["low"]
    h1["body"] = (h1["close"] - h1["open"]).abs()
    h1["body_atr"] = h1["body"] / h1["atr14"].replace(0, np.nan)
    h1["close_location"] = (h1["close"] - h1["low"]) / h1["range"].replace(0, np.nan)
    h1["end"] = h1["time"] + pd.Timedelta(hours=1)

    h4["ema20"] = ema(h4["close"], 20)
    h4["ema50"] = ema(h4["close"], 50)
    h4["available"] = h4["time"] + pd.Timedelta(hours=4)
    h4_available = h4[["available", "close", "ema20", "ema50"]].rename(
        columns={"close": "h4_close", "ema20": "h4_ema20", "ema50": "h4_ema50"}
    )

    d1["ema20"] = ema(d1["close"], 20)
    d1["ema50"] = ema(d1["close"], 50)
    d1["available"] = d1["time"] + pd.Timedelta(days=1)
    d1_available = d1[["available", "close", "ema20", "ema50"]].rename(
        columns={"close": "d1_close", "ema20": "d1_ema20", "ema50": "d1_ema50"}
    )

    h1 = pd.merge_asof(
        h1.sort_values("end"),
        h4_available.sort_values("available"),
        left_on="end",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    h1 = pd.merge_asof(
        h1.sort_values("end"),
        d1_available.sort_values("available"),
        left_on="end",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    return h1.reset_index(drop=True), h4, d1


def _session_ranges(h1: pd.DataFrame, profile: IctProfile) -> pd.DataFrame:
    frame = h1.copy()
    frame["session_date"] = frame["time"].dt.floor("D")
    hours = frame["time"].dt.hour
    session = frame[(hours >= profile.session_start_hour) & (hours < profile.session_end_hour)]
    ranges = session.groupby("session_date", as_index=False).agg(
        session_high=("high", "max"),
        session_low=("low", "min"),
        session_bars=("time", "count"),
    )
    return frame.merge(ranges, on="session_date", how="left")


def _simulate(
    h1: pd.DataFrame,
    signal_index: int,
    side: int,
    stop_price: float,
    target_r: float,
    max_holding_hours: int,
) -> tuple[pd.Timestamp, float]:
    if signal_index + 1 >= len(h1):
        return pd.Timestamp(h1.iloc[signal_index]["end"]), 0.0
    entry_row = h1.iloc[signal_index + 1]
    entry = float(entry_row["open"])
    risk = (entry - stop_price) * side
    if not np.isfinite(risk) or risk <= 0:
        return pd.Timestamp(entry_row["end"]), 0.0
    target = entry + side * target_r * risk
    last_index = min(len(h1) - 1, signal_index + max_holding_hours)
    for index in range(signal_index + 1, last_index + 1):
        row = h1.iloc[index]
        low = float(row["low"])
        high = float(row["high"])
        stop_hit = low <= stop_price if side > 0 else high >= stop_price
        target_hit = high >= target if side > 0 else low <= target
        if stop_hit:
            return pd.Timestamp(row["end"]), -1.0
        if target_hit:
            return pd.Timestamp(row["end"]), float(target_r)
    final = h1.iloc[last_index]
    return pd.Timestamp(final["end"]), float((float(final["close"]) - entry) * side / risk)


def generate_candidates(symbol: str, h1: pd.DataFrame, profile: IctProfile) -> pd.DataFrame:
    symbol = symbol.upper()
    if symbol not in ENGINE_BY_SYMBOL:
        raise ValueError(f"Unsupported ICT shadow symbol: {symbol}")
    frame = _session_ranges(h1, profile)
    hours = frame["time"].dt.hour
    weekdays = frame["time"].dt.weekday
    in_window = (
        (hours >= profile.entry_start_hour)
        & (hours < profile.entry_end_hour)
        & weekdays.isin(profile.allowed_weekdays)
        & (frame["session_bars"] >= max(3, profile.session_end_hour - profile.session_start_hour - 1))
    )

    daily_long = (frame["d1_close"] > frame["d1_ema20"]) & (frame["d1_ema20"] > frame["d1_ema50"])
    daily_short = (frame["d1_close"] < frame["d1_ema20"]) & (frame["d1_ema20"] < frame["d1_ema50"])
    h4_long = (frame["h4_close"] > frame["h4_ema20"]) & (frame["h4_ema20"] > frame["h4_ema50"])
    h4_short = (frame["h4_close"] < frame["h4_ema20"]) & (frame["h4_ema20"] < frame["h4_ema50"])
    if not profile.require_daily_bias:
        daily_long = daily_short = pd.Series(True, index=frame.index)
    if not profile.require_h4_bias:
        h4_long = h4_short = pd.Series(True, index=frame.index)

    sweep_buffer = frame["atr14"] * profile.sweep_atr
    long_signal = (
        in_window
        & daily_long
        & h4_long
        & (frame["low"] <= frame["session_low"] - sweep_buffer)
        & (frame["close"] > frame["session_low"])
        & (frame["close"] > frame["open"])
        & (frame["body_atr"] >= profile.displacement_atr)
        & (frame["close_location"] >= 0.60)
    )
    short_signal = (
        in_window
        & daily_short
        & h4_short
        & (frame["high"] >= frame["session_high"] + sweep_buffer)
        & (frame["close"] < frame["session_high"])
        & (frame["close"] < frame["open"])
        & (frame["body_atr"] >= profile.displacement_atr)
        & (frame["close_location"] <= 0.40)
    )

    rows: list[dict] = []
    last_day: pd.Timestamp | None = None
    for index in np.flatnonzero((long_signal | short_signal).to_numpy()):
        row = frame.iloc[int(index)]
        date = pd.Timestamp(row["session_date"])
        if last_day is not None and date == last_day:
            continue
        side = 1 if bool(long_signal.iloc[int(index)]) else -1
        atr_value = float(row["atr14"])
        if not np.isfinite(atr_value) or atr_value <= 0:
            continue
        stop = (
            min(float(row["low"]), float(row["session_low"])) - profile.stop_buffer_atr * atr_value
            if side > 0
            else max(float(row["high"]), float(row["session_high"])) + profile.stop_buffer_atr * atr_value
        )
        exit_time, result_r = _simulate(
            frame,
            int(index),
            side,
            stop,
            profile.target_r,
            profile.max_holding_hours,
        )
        rows.append(
            {
                "symbol": symbol,
                "engine": ENGINE_BY_SYMBOL[symbol],
                "setup": SETUP_BY_SYMBOL[symbol],
                "profile": profile.name,
                "side": "BUY" if side > 0 else "SELL",
                "entry_time": pd.Timestamp(row["end"]),
                "exit_time": exit_time,
                "r_multiple": float(result_r),
                "session_high": float(row["session_high"]),
                "session_low": float(row["session_low"]),
                "signal_atr": atr_value,
            }
        )
        last_day = date
    return pd.DataFrame(rows)


def performance(frame: pd.DataFrame) -> dict[str, float | int | None]:
    if frame.empty:
        return {"trades": 0, "net_r": 0.0, "profit_factor": 0.0, "win_rate": 0.0}
    values = frame["r_multiple"].astype(float)
    gross_profit = float(values[values > 0].sum())
    gross_loss = float(-values[values < 0].sum())
    return {
        "trades": int(len(frame)),
        "net_r": float(values.sum()),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "win_rate": float((values > 0).mean()),
    }


def select_profile(symbol: str, h1: pd.DataFrame, split: pd.Timestamp) -> tuple[IctProfile, pd.DataFrame, dict]:
    reports: dict[str, dict] = {}
    generated: dict[str, pd.DataFrame] = {}
    for profile in PROFILES[symbol]:
        candidates = generate_candidates(symbol, h1, profile)
        generated[profile.name] = candidates
        development = candidates[candidates["entry_time"] < split]
        validation = candidates[candidates["entry_time"] >= split]
        reports[profile.name] = {
            "profile": asdict(profile),
            "development": performance(development),
            "validation": performance(validation),
            "all": performance(candidates),
        }

    def score(profile: IctProfile) -> tuple[float, float, int]:
        stats = reports[profile.name]["development"]
        trades = int(stats["trades"])
        pf = float(stats["profit_factor"] or 0.0)
        net = float(stats["net_r"])
        if trades < 25 or net <= 0 or pf < 1.02:
            return (-1e9, net, trades)
        return (net * min(pf, 2.0), pf, trades)

    selected = max(PROFILES[symbol], key=score)
    return selected, generated[selected.name], {"selected": selected.name, "profiles": reports}


def validate_registry() -> None:
    expected = {"EURUSD", "AUDUSD", "USDJPY"}
    if set(PROFILES) != expected or set(ENGINE_BY_SYMBOL) != expected:
        raise RuntimeError("All-symbol ICT shadow registry is incomplete")
    for symbol, profiles in PROFILES.items():
        if not profiles:
            raise RuntimeError(f"No ICT profiles for {symbol}")
        for profile in profiles:
            if not (0 < profile.target_r <= 3.0):
                raise RuntimeError(f"Invalid target for {symbol}/{profile.name}")
            if profile.entry_start_hour < profile.session_end_hour:
                raise RuntimeError(f"Entry window overlaps unfinished range for {symbol}/{profile.name}")


validate_registry()
