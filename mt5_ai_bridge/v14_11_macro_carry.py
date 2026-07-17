"""V14.11 macro carry strategy candidates.

This research-only module combines lagged monthly short-rate differentials with
completed daily FX candles. It is independent of the existing SWING and ICT
signal families. Three macro profiles are evaluated:

* CARRY_TREND: rate differential plus long-horizon trend;
* CARRY_BREAKOUT: rate differential plus Donchian expansion;
* CARRY_PULLBACK: rate differential plus a pullback toward the trend EMA.

Every trade enters at the next daily open. Intrabar stop evaluation is
conservative and precedes target evaluation. No account or order API is used.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

PAIR_CURRENCIES = {
    "GBPUSD": ("GBP", "USD"),
    "EURUSD": ("EUR", "USD"),
    "GBPJPY": ("GBP", "JPY"),
    "AUDUSD": ("AUD", "USD"),
    "USDJPY": ("USD", "JPY"),
}


@dataclass(frozen=True)
class CarrySpec:
    family: str
    ema_period: int
    momentum_days: int
    differential_threshold: float
    breakout_days: int
    stop_atr: float
    target_r: float
    max_holding_days: int
    rebalance_days: int = 5
    carry_haircut: float = 0.50
    cost_r: float = 0.10

    @property
    def name(self) -> str:
        return (
            f"{self.family}_E{self.ema_period}_M{self.momentum_days}_"
            f"D{self.differential_threshold:g}_B{self.breakout_days}"
        )


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
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def prepare_daily(
    daily: pd.DataFrame,
    base_rate: pd.DataFrame,
    quote_rate: pd.DataFrame,
) -> pd.DataFrame:
    work = daily.copy().sort_values("time").reset_index(drop=True)
    work["atr14"] = atr(work)
    work["return_20"] = np.log(work["close"] / work["close"].shift(20))
    work["return_60"] = np.log(work["close"] / work["close"].shift(60))
    work["return_120"] = np.log(work["close"] / work["close"].shift(120))
    for period in (50, 100, 200):
        work[f"ema_{period}"] = work["close"].ewm(
            span=period, adjust=False, min_periods=period
        ).mean()
    for period in (20, 60):
        work[f"prior_high_{period}"] = work["high"].rolling(
            period, min_periods=period
        ).max().shift(1)
        work[f"prior_low_{period}"] = work["low"].rolling(
            period, min_periods=period
        ).min().shift(1)

    rates = pd.merge_asof(
        base_rate.sort_values("available_date"),
        quote_rate.sort_values("available_date"),
        on="available_date",
        direction="nearest",
        tolerance=pd.Timedelta(days=20),
        suffixes=("_base", "_quote"),
    ).dropna(subset=["rate_percent_base", "rate_percent_quote"])
    rates["differential_percent"] = (
        rates["rate_percent_base"] - rates["rate_percent_quote"]
    )
    work = pd.merge_asof(
        work.sort_values("time"),
        rates[["available_date", "differential_percent"]].sort_values(
            "available_date"
        ),
        left_on="time",
        right_on="available_date",
        direction="backward",
    )
    return work.drop(columns=["available_date"]).sort_values("time").reset_index(
        drop=True
    )


def simulate_exit(
    frame: pd.DataFrame,
    signal_index: int,
    side: int,
    spec: CarrySpec,
) -> tuple[pd.Timestamp, float, int, float]:
    entry_index = signal_index + 1
    if entry_index >= len(frame):
        row = frame.iloc[signal_index]
        return pd.Timestamp(row["time"]), 0.0, 0, 0.0
    signal = frame.iloc[signal_index]
    entry_row = frame.iloc[entry_index]
    entry = float(entry_row["open"])
    atr_value = float(signal["atr14"])
    if not np.isfinite(entry) or not np.isfinite(atr_value) or atr_value <= 0:
        return pd.Timestamp(entry_row["time"]), 0.0, 0, 0.0
    distance = float(spec.stop_atr) * atr_value
    stop = entry - side * distance
    target = entry + side * float(spec.target_r) * distance
    last = min(len(frame) - 1, entry_index + int(spec.max_holding_days) - 1)
    exit_price = float(frame.iloc[last]["close"])
    exit_index = last

    for index in range(entry_index, last + 1):
        row = frame.iloc[index]
        low, high = float(row["low"]), float(row["high"])
        stop_hit = low <= stop if side > 0 else high >= stop
        if stop_hit:
            exit_price = stop
            exit_index = index
            break
        target_hit = high >= target if side > 0 else low <= target
        if target_hit:
            exit_price = target
            exit_index = index
            break

    price_r = (exit_price - entry) * side / distance
    holding_days = max(1, exit_index - entry_index + 1)
    differential = abs(float(signal["differential_percent"])) / 100.0
    stop_fraction = distance / entry
    carry_r = 0.0
    if stop_fraction > 0:
        carry_r = (
            differential
            * holding_days
            / 365.0
            / stop_fraction
            * float(spec.carry_haircut)
        )
    exit_time = pd.Timestamp(frame.iloc[exit_index]["time"]) + pd.Timedelta(days=1)
    return exit_time, float(price_r + carry_r), holding_days, float(carry_r)


def generate_candidates(
    symbol: str,
    daily: pd.DataFrame,
    base_rate: pd.DataFrame,
    quote_rate: pd.DataFrame,
    spec: CarrySpec,
) -> pd.DataFrame:
    frame = prepare_daily(daily, base_rate, quote_rate)
    momentum_column = f"return_{spec.momentum_days}"
    ema_column = f"ema_{spec.ema_period}"
    high_column = f"prior_high_{spec.breakout_days}"
    low_column = f"prior_low_{spec.breakout_days}"
    rows: list[dict[str, object]] = []
    last_exit = pd.Timestamp.min.tz_localize("UTC")
    last_entry_index = -10_000

    for index in range(max(220, spec.momentum_days + 2), len(frame) - 1):
        row = frame.iloc[index]
        now = pd.Timestamp(row["time"])
        if now < last_exit or index - last_entry_index < spec.rebalance_days:
            continue
        differential = float(row["differential_percent"])
        if not np.isfinite(differential) or abs(differential) < spec.differential_threshold:
            continue
        side = 1 if differential > 0 else -1
        momentum = float(row[momentum_column])
        close = float(row["close"])
        ema_value = float(row[ema_column])
        if not all(np.isfinite([momentum, close, ema_value, row["atr14"]])):
            continue
        trend_ok = close > ema_value and momentum > 0 if side > 0 else close < ema_value and momentum < 0
        if not trend_ok:
            continue

        if spec.family == "CARRY_BREAKOUT":
            trigger = close > float(row[high_column]) if side > 0 else close < float(row[low_column])
        elif spec.family == "CARRY_PULLBACK":
            distance_to_ema = abs(close - ema_value) / float(row["atr14"])
            candle_confirmation = close > float(row["open"]) if side > 0 else close < float(row["open"])
            trigger = distance_to_ema <= 0.75 and candle_confirmation
        elif spec.family == "CARRY_TREND":
            trigger = abs(momentum) >= 0.015
        else:
            raise ValueError(f"Unknown carry family: {spec.family}")
        if not trigger:
            continue

        exit_time, result_r, holding_days, carry_r = simulate_exit(
            frame, index, side, spec
        )
        entry_time = pd.Timestamp(frame.iloc[index + 1]["time"])
        rows.append(
            {
                "symbol": symbol,
                "mode": "MACRO_CARRY",
                "engine": f"{symbol}_MACRO_CARRY_{spec.family}",
                "family": spec.family,
                "profile": spec.name,
                "timeframe": "D1",
                "side": "BUY" if side > 0 else "SELL",
                "entry_time": entry_time,
                "exit_time": exit_time,
                "r_multiple": result_r,
                "selection_cost_r": float(spec.cost_r),
                "rate_differential_percent": differential,
                "carry_r": carry_r,
                "holding_days": holding_days,
                "stop_atr": float(spec.stop_atr),
                "target_r": float(spec.target_r),
            }
        )
        last_entry_index = index
        last_exit = exit_time
    return pd.DataFrame(rows)


def candidate_specs() -> tuple[CarrySpec, ...]:
    specs: list[CarrySpec] = []
    geometry = {
        "CARRY_TREND": (2.0, 3.0, 40),
        "CARRY_BREAKOUT": (2.0, 3.5, 60),
        "CARRY_PULLBACK": (1.5, 2.5, 30),
    }
    for family, (stop_atr, target_r, hold) in geometry.items():
        for ema_period in (50, 100, 200):
            for momentum_days in (20, 60, 120):
                for threshold in (0.25, 0.50, 1.00):
                    for breakout_days in ((20, 60) if family == "CARRY_BREAKOUT" else (20,)):
                        specs.append(
                            CarrySpec(
                                family=family,
                                ema_period=ema_period,
                                momentum_days=momentum_days,
                                differential_threshold=threshold,
                                breakout_days=breakout_days,
                                stop_atr=stop_atr,
                                target_r=target_r,
                                max_holding_days=hold,
                            )
                        )
    return tuple(specs)


def validate_specs() -> None:
    for spec in candidate_specs():
        if spec.family not in {"CARRY_TREND", "CARRY_BREAKOUT", "CARRY_PULLBACK"}:
            raise RuntimeError(f"Invalid family: {spec}")
        if spec.ema_period not in {50, 100, 200}:
            raise RuntimeError(f"Invalid EMA: {spec}")
        if spec.momentum_days not in {20, 60, 120}:
            raise RuntimeError(f"Invalid momentum: {spec}")
        if spec.cost_r < 0 or spec.carry_haircut > 0.50:
            raise RuntimeError(f"Invalid cost/carry assumptions: {spec}")


validate_specs()
