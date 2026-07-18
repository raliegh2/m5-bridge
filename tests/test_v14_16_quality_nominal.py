from __future__ import annotations

from datetime import datetime, timezone

from mt5_ai_bridge.v14_13_cost_regime_profile import CostRegimeDecision
from mt5_ai_bridge.v14_16_quality_nominal import (
    frozen_nominal_risk_percent,
    strict_quality_risk_target,
)

NOW = datetime(2026, 7, 18, 16, tzinfo=timezone.utc)


def funded(risk: float) -> CostRegimeDecision:
    return CostRegimeDecision(
        funded=True,
        regime="EXTENDED_COST_V12",
        risk_percent=risk,
        reason="validated",
        all_in_cost_r=0.02,
        target_r=3.0,
    )


def test_frozen_nominal_resolves_documented_v12_tier() -> None:
    assert frozen_nominal_risk_percent(
        "GBPUSD_V10_PRECISION",
        "PRIMARY_16UTC_BREAKOUT",
        0.20,
    ) == 0.50
    assert frozen_nominal_risk_percent(
        "AUDUSD_TREND_PULLBACK",
        "D1_H4_EMA_PULLBACK_04_08UTC",
        0.025,
    ) == 0.55


def test_reduced_gbpusd_v12_candidate_cannot_be_promoted() -> None:
    target, reason = strict_quality_risk_target(
        symbol="GBPUSD",
        engine="GBPUSD_V10_PRECISION",
        setup="PRIMARY_16UTC_BREAKOUT",
        mode="V12",
        side="BUY",
        entry_time=NOW,
        all_in_cost_r=0.02,
        nominal_risk_percent=0.20,
        current_risk_percent=0.20,
        current_decision=funded(0.20),
        historical_profile_authorized=True,
    )
    assert target is None
    assert reason == "EXISTING_RISK_REDUCTION_PRESERVED"


def test_reduced_audusd_v12_candidate_cannot_be_promoted() -> None:
    target, reason = strict_quality_risk_target(
        symbol="AUDUSD",
        engine="AUDUSD_TREND_PULLBACK",
        setup="D1_H4_EMA_PULLBACK_04_08UTC",
        mode="V12",
        side="BUY",
        entry_time=NOW,
        all_in_cost_r=0.02,
        nominal_risk_percent=0.025,
        current_risk_percent=0.025,
        current_decision=funded(0.025),
        historical_profile_authorized=True,
    )
    assert target is None
    assert reason == "EXISTING_RISK_REDUCTION_PRESERVED"
