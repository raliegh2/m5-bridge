"""Frozen nominal-risk resolver for V14.16 quality allocation.

Some historical V12 rows already contain reduced source risk.  Those values are
not the setup's full-strength allocation and must never be used as the promotion
baseline.  This wrapper substitutes the documented nominal tier for selected
quality profiles before delegating to the core V14.16 decision.
"""
from __future__ import annotations

from typing import Any

from .v14_16_quality_allocation import quality_risk_target

QUALITY_NOMINAL_RISK_PERCENT: dict[tuple[str, str], float] = {
    ("GBPUSD_V10_PRECISION", "PRIMARY_16UTC_BREAKOUT"): 0.50,
    ("GBPUSD_V10_PRECISION", "SECONDARY_12UTC_BREAKOUT"): 0.40,
    ("GBPUSD_V10_PRECISION", "GBPUSD_SWING_V5_PULLBACK_ADDON"): 0.40,
    ("EURUSD_SWING_CORE", "H4_DONCHIAN_BREAKOUT"): 0.55,
    ("AUDUSD_TREND_PULLBACK", "D1_H4_EMA_PULLBACK_04_08UTC"): 0.55,
    ("EURUSD_ICT_LIQUIDITY", "eurusd_ict_liquidity"): 0.55,
    ("AUDUSD_ICT_ASIA_LONDON", "audusd_ict_asia_london"): 0.45,
}


def frozen_nominal_risk_percent(
    engine: str,
    setup: str,
    fallback: float,
) -> float:
    return float(
        QUALITY_NOMINAL_RISK_PERCENT.get(
            (str(engine), str(setup)),
            max(0.0, float(fallback)),
        )
    )


def strict_quality_risk_target(**kwargs: Any) -> tuple[float | None, str]:
    values = dict(kwargs)
    values["nominal_risk_percent"] = frozen_nominal_risk_percent(
        str(values.get("engine", "")),
        str(values.get("setup", "")),
        float(values.get("nominal_risk_percent", 0.0) or 0.0),
    )
    return quality_risk_target(**values)
