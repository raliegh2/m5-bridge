"""Frozen V14.5.4 selective ICT profit-sleeve profile.

The V14.5.2 swing core remains unchanged. Legacy GBP M1 ICT and unpromoted
wide-stop ICT streams stay at 0.025% observation risk. Only two H1 ICT
profiles passed cost-adjusted development, confirmation and holdout gates:

* EURUSD / eu_ny_20 at 0.25% risk;
* AUDUSD / au_london_relaxed at 0.15% risk.

This module contains no broker or order API. Live integration remains blocked
until READ_ONLY/demo forward validation is completed.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from mt5_ai_bridge.v14_3_satellite_symbol_profile import (
    SATELLITE_GUARDS,
    filter_satellite_ict,
)
from mt5_ai_bridge.v14_5_cost_robust_profile import V14_5_OBSERVATION_RISK_PERCENT

V14_5_4_OBSERVATION_RISK_PERCENT = float(V14_5_OBSERVATION_RISK_PERCENT)
V14_5_4_MAX_SELECTIVE_ICT_RISK_PERCENT = 0.35

PROMOTED_WIDE_ICT_RISK: dict[tuple[str, str], float] = {
    ("EURUSD", "eu_ny_20"): 0.25,
    ("AUDUSD", "au_london_relaxed"): 0.15,
}

SELECTIVE_ICT_GUARDS = {
    "EURUSD": SATELLITE_GUARDS["EURUSD"],
    "AUDUSD": SATELLITE_GUARDS["AUDUSD"],
    "USDJPY": SATELLITE_GUARDS["USDJPY"],
}


def selective_setup_name(symbol: str, profile: str) -> str:
    """Return the stable setup key stored in risk and execution state."""
    return f"v14_5_4_{symbol.lower()}_{profile.lower()}"


def selective_ict_risk_percent(symbol: str, profile: str | None) -> float:
    """Return promoted risk or the unchanged observation tier."""
    key = (str(symbol).upper(), str(profile or ""))
    return float(PROMOTED_WIDE_ICT_RISK.get(key, V14_5_4_OBSERVATION_RISK_PERCENT))


def selective_ict_tier(symbol: str, profile: str | None) -> str:
    key = (str(symbol).upper(), str(profile or ""))
    return "PROMOTED_WIDE_ICT" if key in PROMOTED_WIDE_ICT_RISK else "ICT_OBSERVATION"


def apply_selective_ict_profile(candidates: pd.DataFrame) -> pd.DataFrame:
    """Apply frozen quality filters and attach V14.5.4 risk/tier metadata."""
    if candidates.empty:
        return candidates.copy()
    filtered = filter_satellite_ict(candidates)
    output = filtered.copy()
    output["v14_5_4_tier"] = output.apply(
        lambda row: selective_ict_tier(str(row["symbol"]), str(row.get("profile", ""))),
        axis=1,
    )
    output["risk_percent"] = output.apply(
        lambda row: selective_ict_risk_percent(
            str(row["symbol"]), str(row.get("profile", ""))
        ),
        axis=1,
    )
    output["original_setup"] = output["setup"].astype(str)
    promoted = output[output["v14_5_4_tier"] == "PROMOTED_WIDE_ICT"].copy()
    promoted["setup"] = promoted.apply(
        lambda row: selective_setup_name(str(row["symbol"]), str(row["profile"])),
        axis=1,
    )
    return promoted.sort_values(["entry_time", "symbol", "engine"]).reset_index(drop=True)


def validate_profile() -> None:
    if not PROMOTED_WIDE_ICT_RISK:
        raise RuntimeError("V14.5.4 requires at least one promoted ICT profile")
    if max(PROMOTED_WIDE_ICT_RISK.values()) > V14_5_4_MAX_SELECTIVE_ICT_RISK_PERCENT:
        raise RuntimeError("Selective ICT risk exceeds the 0.35% ceiling")
    if V14_5_4_OBSERVATION_RISK_PERCENT > 0.05:
        raise RuntimeError("Observation risk must remain micro-sized")
    if set(SELECTIVE_ICT_GUARDS) != {"EURUSD", "AUDUSD", "USDJPY"}:
        raise RuntimeError("Selective ICT guard coverage is incomplete")
    if any(guard.max_open_positions != 1 for guard in SELECTIVE_ICT_GUARDS.values()):
        raise RuntimeError("Each selective ICT symbol must retain one-position control")
    if any(guard.max_entries_per_hour != 1 for guard in SELECTIVE_ICT_GUARDS.values()):
        raise RuntimeError("Each selective ICT symbol must retain one-entry-per-hour control")


validate_profile()
