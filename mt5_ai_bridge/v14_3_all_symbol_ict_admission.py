"""Frozen cross-window admission filters for the three new ICT shadow engines.

The filters were required to remain profitable across early development,
confirmation and later validation partitions. They use only signal metadata
known at entry: symbol, completed-candle hour, weekday and direction.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ShadowAdmission:
    profile_name: str
    completed_entry_hours_utc: tuple[int, ...]
    weekdays_utc: tuple[int, ...]
    sides: tuple[str, ...]


ADMISSION: dict[str, ShadowAdmission] = {
    "EURUSD": ShadowAdmission(
        profile_name="eu_ny_20",
        completed_entry_hours_utc=(14, 17),
        weekdays_utc=(1, 2, 3, 4),
        sides=("BUY", "SELL"),
    ),
    "AUDUSD": ShadowAdmission(
        profile_name="au_london_relaxed",
        completed_entry_hours_utc=(8, 9, 10, 11, 12),
        weekdays_utc=(0, 1, 2, 3, 4),
        sides=("SELL",),
    ),
    "USDJPY": ShadowAdmission(
        profile_name="uj_ny_relaxed",
        completed_entry_hours_utc=(13, 15, 16, 18),
        weekdays_utc=(1, 2, 3, 4),
        sides=("SELL",),
    ),
}


def apply_shadow_admission(symbol: str, candidates: pd.DataFrame) -> pd.DataFrame:
    symbol = symbol.upper()
    rule = ADMISSION[symbol]
    if candidates.empty:
        return candidates.copy()
    output = candidates.copy()
    times = pd.to_datetime(output["entry_time"], utc=True)
    mask = (
        times.dt.hour.isin(rule.completed_entry_hours_utc)
        & times.dt.weekday.isin(rule.weekdays_utc)
        & output["side"].astype(str).str.upper().isin(rule.sides)
    )
    output = output.loc[mask].copy()
    output["admission_profile"] = rule.profile_name
    output["admission_reason"] = "CROSS_WINDOW_STABLE_ICT"
    return output.reset_index(drop=True)


def validate_admission() -> None:
    if set(ADMISSION) != {"EURUSD", "AUDUSD", "USDJPY"}:
        raise RuntimeError("ICT admission registry must cover all new symbols")
    for symbol, rule in ADMISSION.items():
        if not rule.completed_entry_hours_utc or not rule.weekdays_utc or not rule.sides:
            raise RuntimeError(f"Incomplete ICT admission rule for {symbol}")
        if any(hour < 0 or hour > 23 for hour in rule.completed_entry_hours_utc):
            raise RuntimeError(f"Invalid UTC hour for {symbol}")


validate_admission()
