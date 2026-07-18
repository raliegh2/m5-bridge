"""V14.5.2 cost-robust profitability filter used by the live V14.12 overlay.

The filter retains the V14.5 cost-robust engine allocation and demotes only two
pre-entry buckets that were negative in both development and validation. A
demoted trade remains at observation risk so live after-cost evidence continues
to accumulate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mt5_ai_bridge.v14_5_cost_robust_profile import (
    PARITY_TRADE_RISK_CEILING_PERCENT,
    PROMOTED_V12_ENGINES,
    V14_5_OBSERVATION_RISK_PERCENT,
    v14_5_risk_percent,
)

V14_5_2_OBSERVATION_RISK_PERCENT = V14_5_OBSERVATION_RISK_PERCENT
EURUSD_WEAK_ENTRY_HOURS_UTC: frozenset[int] = frozenset({16})
GBPJPY_WEAK_WEEKDAYS_UTC: frozenset[int] = frozenset({1})


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif hasattr(value, "to_pydatetime"):
        result = value.to_pydatetime()
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported entry time type: {type(value)!r}")
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def v14_5_2_filter_reason(engine: str, entry_time: Any) -> str | None:
    timestamp = _utc_datetime(entry_time)
    if str(engine) == "EURUSD_SWING_CORE" and timestamp.hour in EURUSD_WEAK_ENTRY_HOURS_UTC:
        return "EURUSD_16UTC_OBSERVATION"
    if str(engine) == "GBPJPY_SWING_CORE" and timestamp.weekday() in GBPJPY_WEAK_WEEKDAYS_UTC:
        return "GBPJPY_TUESDAY_OBSERVATION"
    return None


def v14_5_2_risk_percent(engine: str, mode: str, entry_time: Any) -> float:
    base = float(v14_5_risk_percent(engine, mode))
    if mode.upper() != "V12":
        return base
    if str(engine) not in PROMOTED_V12_ENGINES:
        return base
    if v14_5_2_filter_reason(engine, entry_time) is not None:
        return V14_5_2_OBSERVATION_RISK_PERCENT
    return base


def validate_profile() -> None:
    if V14_5_2_OBSERVATION_RISK_PERCENT > 0.05:
        raise RuntimeError("V14.5.2 observation risk must remain micro-sized")
    if PARITY_TRADE_RISK_CEILING_PERCENT != 0.80:
        raise RuntimeError("Unexpected parity trade-risk ceiling")
    if not GBPJPY_WEAK_WEEKDAYS_UTC <= set(range(7)):
        raise RuntimeError("Invalid GBPJPY weekday filter")
    if not EURUSD_WEAK_ENTRY_HOURS_UTC <= set(range(24)):
        raise RuntimeError("Invalid EURUSD hour filter")


validate_profile()
