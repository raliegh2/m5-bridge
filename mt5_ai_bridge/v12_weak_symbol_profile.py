"""Frozen pre-entry risk policy for the weaker V12 symbol engines.

This profile does not invent trade outcomes or modify the V12 signal criteria.
It reallocates the risk already requested by the V12 ledger using engine and
completed-candle time information available before entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class WeakSymbolPolicy:
    full_risk_multiplier: float
    full_risk_cap: float
    micro_risk_cap: float
    quality_hours_utc: tuple[int, ...] = ()


POLICIES: dict[str, WeakSymbolPolicy] = {
    # The quality-gated core was the persistent EURUSD contributor; the retest
    # remains enabled at micro risk instead of being removed from the engine set.
    "EURUSD_CORE": WeakSymbolPolicy(1.40, 0.35, 0.05),
    "EURUSD_RETEST": WeakSymbolPolicy(1.00, 0.10, 0.05),
    # AUDUSD's independently validated continuation profile was strongest on
    # the completed 08:00 UTC H4 signal. The 04:00 stream remains a micro probe.
    "AUDUSD": WeakSymbolPolicy(1.40, 0.35, 0.05, (8,)),
    # USDJPY receives full allocation only in the cross-window positive
    # completed-H4 quality hours. Other signals remain observable at micro risk.
    "USDJPY": WeakSymbolPolicy(1.55, 0.25, 0.05, (0, 16, 20)),
}


def _hour(value: Any) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return int(stamp.hour)


def adjusted_v12_risk_percent(row: dict[str, Any]) -> tuple[float, str]:
    """Return adjusted risk and a machine-readable quality tier."""
    original = max(0.0, float(row["risk_percent"]))
    symbol = str(row["symbol"]).upper()
    engine = str(row["engine"]).upper()

    if symbol == "EURUSD":
        if engine == "EURUSD_SWING_CORE":
            policy = POLICIES["EURUSD_CORE"]
            return min(original * policy.full_risk_multiplier, policy.full_risk_cap), "EURUSD_CORE_FULL"
        policy = POLICIES["EURUSD_RETEST"]
        return min(original, policy.micro_risk_cap), "EURUSD_RETEST_MICRO"

    if symbol == "AUDUSD":
        policy = POLICIES["AUDUSD"]
        if _hour(row["entry_time"]) in policy.quality_hours_utc:
            return min(original * policy.full_risk_multiplier, policy.full_risk_cap), "AUDUSD_08UTC_FULL"
        return min(original, policy.micro_risk_cap), "AUDUSD_04UTC_MICRO"

    if symbol == "USDJPY":
        policy = POLICIES["USDJPY"]
        if _hour(row["entry_time"]) in policy.quality_hours_utc:
            return min(original * policy.full_risk_multiplier, policy.full_risk_cap), "USDJPY_00_16_20UTC_FULL"
        return min(original, policy.micro_risk_cap), "USDJPY_OTHER_HOUR_MICRO"

    return original, "UNCHANGED"


def apply_weak_symbol_profile(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["original_risk_percent"] = output["risk_percent"].astype(float)
    adjustments = [adjusted_v12_risk_percent(row) for row in output.to_dict("records")]
    output["risk_percent"] = [item[0] for item in adjustments]
    output["v12_quality_tier"] = [item[1] for item in adjustments]
    return output


def validate_profile() -> None:
    assert POLICIES["EURUSD_CORE"].full_risk_cap == 0.35
    assert POLICIES["AUDUSD"].quality_hours_utc == (8,)
    assert POLICIES["USDJPY"].quality_hours_utc == (0, 16, 20)
    assert all(policy.micro_risk_cap <= 0.05 for key, policy in POLICIES.items() if key != "EURUSD_CORE")


validate_profile()
