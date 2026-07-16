"""Frozen V14.6.2 validated intraday ICT profile.

The raw-candle research engine may produce many completed-H1 candidates per
day. Only the two components that passed development, confirmation and holdout
after the 0.12R retail allowance are promoted:

* GBPJPY London pullback, SELL at 14:00 UTC: 0.20% risk;
* AUDUSD Asia-London pullback, BUY at 07:00 UTC: 0.20% risk.

GBPUSD and every other intraday component remain shadow/observation only. This
separates signal frequency from paid execution frequency: the system can learn
from all setups without repeatedly paying spread, commission and slippage on
an unvalidated stream.

No broker or order API is imported here. Live integration remains blocked
until demo forward validation is completed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

V14_6_2_PROMOTED_RISK_PERCENT = 0.20
V14_6_2_OBSERVATION_RISK_PERCENT = 0.025
V14_6_2_MAX_OPEN_ICT_POSITIONS_PER_TARGET = 2
V14_6_2_MAX_ENTRIES_PER_HOUR = 1
V14_6_2_ICT_OPEN_RISK_CAP_PERCENT = 1.75
V14_6_2_COMBINED_OPEN_RISK_CAP_PERCENT = 3.25


@dataclass(frozen=True)
class PromotedIntradayICT:
    symbol: str
    engine: str
    side: str
    hour_utc: int
    risk_percent: float


PROMOTED_INTRADAY_ICT: dict[str, PromotedIntradayICT] = {
    "GBPJPY": PromotedIntradayICT(
        symbol="GBPJPY",
        engine="GBPJPY_ICT_INTRADAY_GJ_LONDON_PULLBACK",
        side="SELL",
        hour_utc=14,
        risk_percent=V14_6_2_PROMOTED_RISK_PERCENT,
    ),
    "AUDUSD": PromotedIntradayICT(
        symbol="AUDUSD",
        engine="AUDUSD_ICT_INTRADAY_AU_ASIA_LONDON_PULLBACK",
        side="BUY",
        hour_utc=7,
        risk_percent=V14_6_2_PROMOTED_RISK_PERCENT,
    ),
}


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif hasattr(value, "to_pydatetime"):
        result = value.to_pydatetime()
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported entry time: {type(value)!r}")
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def is_promoted_intraday_ict(
    symbol: str,
    engine: str,
    side: str,
    entry_time: Any,
) -> bool:
    profile = PROMOTED_INTRADAY_ICT.get(str(symbol).upper())
    if profile is None:
        return False
    timestamp = _utc(entry_time)
    return (
        str(engine) == profile.engine
        and str(side).upper() == profile.side
        and timestamp.hour == profile.hour_utc
    )


def intraday_ict_tier(
    symbol: str,
    engine: str,
    side: str,
    entry_time: Any,
) -> str:
    return (
        "PROMOTED_INTRADAY_ICT"
        if is_promoted_intraday_ict(symbol, engine, side, entry_time)
        else "SHADOW_OBSERVATION"
    )


def intraday_ict_risk_percent(
    symbol: str,
    engine: str,
    side: str,
    entry_time: Any,
) -> float:
    if is_promoted_intraday_ict(symbol, engine, side, entry_time):
        return V14_6_2_PROMOTED_RISK_PERCENT
    return V14_6_2_OBSERVATION_RISK_PERCENT


def validate_profile() -> None:
    if set(PROMOTED_INTRADAY_ICT) != {"GBPJPY", "AUDUSD"}:
        raise RuntimeError("Only the two validated V14.6.2 symbols may be promoted")
    if any(item.risk_percent != 0.20 for item in PROMOTED_INTRADAY_ICT.values()):
        raise RuntimeError("Unexpected promoted intraday ICT risk")
    if V14_6_2_OBSERVATION_RISK_PERCENT > 0.05:
        raise RuntimeError("Observation risk is not micro-sized")
    if V14_6_2_ICT_OPEN_RISK_CAP_PERCENT != 1.75:
        raise RuntimeError("ICT portfolio cap changed")
    if V14_6_2_COMBINED_OPEN_RISK_CAP_PERCENT != 3.25:
        raise RuntimeError("Combined portfolio cap changed")


validate_profile()
