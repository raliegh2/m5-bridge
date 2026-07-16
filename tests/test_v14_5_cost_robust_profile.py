from __future__ import annotations

from mt5_ai_bridge.v14_5_cost_robust_profile import (
    DEMOTED_V12_ENGINES,
    PROMOTED_V12_ENGINES,
    V14_5_OBSERVATION_RISK_PERCENT,
    V14_5_PROMOTED_RISK_PERCENT,
    v14_5_risk_percent,
    validate_profile,
)


def test_promoted_engines_get_full_risk() -> None:
    for engine in PROMOTED_V12_ENGINES:
        assert v14_5_risk_percent(engine, "V12") == V14_5_PROMOTED_RISK_PERCENT


def test_demoted_and_ict_get_observation_risk() -> None:
    for engine in DEMOTED_V12_ENGINES:
        assert v14_5_risk_percent(engine, "V12") == V14_5_OBSERVATION_RISK_PERCENT
    assert v14_5_risk_percent("ICT_V14_3_GBPUSD", "ICT") == V14_5_OBSERVATION_RISK_PERCENT


def test_profile_invariants() -> None:
    validate_profile()
    assert V14_5_PROMOTED_RISK_PERCENT <= 0.80
    assert not (PROMOTED_V12_ENGINES & DEMOTED_V12_ENGINES)
