from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_ai_bridge.v14_13_cost_regime_profile import CostRegimeDecision
from mt5_ai_bridge.v14_16_quality_allocation import (
    QUALITY_RISK_PERCENT,
    apply_quality_allocation,
    live_quality_evidence,
    quality_profile,
    quality_risk_target,
)

NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


def decision(regime: str = "EXTENDED_COST_V12", risk: float = 0.50):
    return CostRegimeDecision(
        funded=True,
        regime=regime,
        risk_percent=risk,
        reason="validated",
        all_in_cost_r=0.075,
        target_r=3.0,
    )


def target(**overrides):
    values = {
        "symbol": "GBPUSD",
        "engine": "GBPUSD_V10_PRECISION",
        "setup": "PRIMARY_16UTC_BREAKOUT",
        "mode": "V12",
        "side": "BUY",
        "entry_time": NOW,
        "all_in_cost_r": 0.02,
        "nominal_risk_percent": 0.50,
        "current_risk_percent": 0.50,
        "current_decision": decision(),
        "historical_profile_authorized": True,
    }
    values.update(overrides)
    return quality_risk_target(**values)


def test_quality_target_uses_existing_single_trade_ceiling() -> None:
    value, reason = target()
    assert value == pytest.approx(QUALITY_RISK_PERCENT)
    assert reason == "GBPUSD_V10_PRECISION_QUALITY"


def test_zero_cost_parity_is_not_uplifted() -> None:
    value, reason = target(all_in_cost_r=0.0)
    assert value is None
    assert reason == "ZERO_COST_PARITY_UNCHANGED"


def test_existing_reduction_is_never_reversed() -> None:
    value, reason = target(current_risk_percent=0.25)
    assert value is None
    assert reason == "EXISTING_RISK_REDUCTION_PRESERVED"


def test_probation_and_observation_are_not_uplifted() -> None:
    for regime in ("OBSERVATION", "DUAL_ENGINE_PROBATION", "REASONING_REDUCED"):
        value, reason = target(
            current_risk_percent=0.50,
            current_decision=decision(regime=regime, risk=0.50),
        )
        assert value is None
        assert reason == "REDUCED_OR_PROBATION_REGIME"


def test_gbp_ict_requires_strict_pre_entry_profile() -> None:
    strict = quality_profile(
        symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup="breakout_15_fade",
        mode="ICT",
        side="SELL",
        entry_time="2026-07-18T12:00:00+00:00",
    )
    noncore = quality_profile(
        symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup="breakout_30_fade",
        mode="ICT",
        side="SELL",
        entry_time="2026-07-18T12:00:00+00:00",
    )
    assert strict is not None
    assert noncore is None


def test_audusd_ict_excludes_weak_10utc_concentration() -> None:
    assert quality_profile(
        symbol="AUDUSD",
        engine="AUDUSD_ICT_ASIA_LONDON",
        setup="audusd_ict_asia_london",
        mode="ICT",
        side="SELL",
        entry_time="2026-07-18T10:00:00+00:00",
    ) is None
    assert quality_profile(
        symbol="AUDUSD",
        engine="AUDUSD_ICT_ASIA_LONDON",
        setup="audusd_ict_asia_london",
        mode="ICT",
        side="SELL",
        entry_time="2026-07-18T11:00:00+00:00",
    ) is not None


def test_live_uplift_requires_mature_engine_and_sleeve_evidence() -> None:
    allowed, reason = live_quality_evidence(
        [0.50, -0.10] * 8,
        [0.40, -0.05] * 10,
    )
    assert allowed
    assert reason == "BROKER_NET_QUALITY_CONFIRMED"

    allowed, reason = live_quality_evidence(
        [0.50, -0.10] * 5,
        [0.40, -0.05] * 10,
    )
    assert not allowed
    assert reason == "ENGINE_SAMPLE_BELOW_12"


def test_apply_quality_allocation_never_exceeds_080_percent() -> None:
    upgraded = apply_quality_allocation(
        decision(),
        target_risk_percent=1.50,
        reason="test",
    )
    assert upgraded.funded
    assert upgraded.regime == "QUALITY_ALLOCATED"
    assert upgraded.risk_percent == pytest.approx(0.80)
