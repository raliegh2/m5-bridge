from __future__ import annotations

from datetime import datetime, timezone

from mt5_ai_bridge.v14_14_extended_cost_profile import (
    ExtendedCostRegimeConfig,
    extended_cost_regime_decision,
)

NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


def decide(**overrides):
    values = {
        "symbol": "GBPUSD",
        "engine": "ICT_V14_3_GBPUSD",
        "setup": "breakout_15_fade",
        "mode": "ICT",
        "side": "SELL",
        "entry_time": NOW,
        "base_risk_percent": 0.455,
        "all_in_cost": 0.28,
        "target_r": 1.25,
        "config": ExtendedCostRegimeConfig(),
    }
    values.update(overrides)
    return extended_cost_regime_decision(**values)


def test_extended_policy_applies_to_ict() -> None:
    decision = decide()
    assert decision.funded
    assert decision.regime == "EXTREME_COST_GBP"
    assert decision.risk_percent == 0.455


def test_extended_policy_applies_to_v12() -> None:
    decision = decide(
        symbol="EURUSD",
        engine="EURUSD_SWING_CORE",
        setup="H4_DONCHIAN_BREAKOUT",
        mode="V12",
        side="BUY",
        base_risk_percent=0.55,
        all_in_cost=0.10,
        target_r=3.0,
    )
    assert decision.funded
    assert decision.regime == "EXTENDED_COST_V12"
    assert decision.risk_percent == 0.55


def test_v12_above_010r_is_shadow() -> None:
    decision = decide(
        symbol="EURUSD",
        engine="EURUSD_SWING_CORE",
        setup="H4_DONCHIAN_BREAKOUT",
        mode="V12",
        side="BUY",
        base_risk_percent=0.55,
        all_in_cost=0.101,
        target_r=3.0,
    )
    assert decision.is_shadow
    assert decision.reason == "V12_COST_ABOVE_VALIDATED_LIMIT"


def test_satellite_ict_is_funded_to_023r() -> None:
    decision = decide(
        symbol="AUDUSD",
        engine="AUDUSD_ICT_ASIA_LONDON",
        setup="audusd_ict_asia_london",
        side="SELL",
        base_risk_percent=0.45,
        all_in_cost=0.23,
        target_r=1.5,
    )
    assert decision.funded
    assert decision.regime == "EXTENDED_COST_SATELLITE"


def test_satellite_ict_above_023r_is_shadow() -> None:
    decision = decide(
        symbol="AUDUSD",
        engine="AUDUSD_ICT_ASIA_LONDON",
        setup="audusd_ict_asia_london",
        side="SELL",
        base_risk_percent=0.45,
        all_in_cost=0.231,
        target_r=1.5,
    )
    assert decision.is_shadow


def test_strict_gbp_above_028r_is_shadow() -> None:
    decision = decide(all_in_cost=0.281)
    assert decision.is_shadow
    assert decision.reason == "STRICT_GBP_COST_ABOVE_VALIDATED_LIMIT"


def test_noncore_gbp_does_not_gain_extended_funding() -> None:
    decision = decide(setup="breakout_30_fade", all_in_cost=0.20)
    assert decision.is_shadow


def test_risk_never_exceeds_frozen_base() -> None:
    decision = decide(base_risk_percent=0.32)
    assert decision.risk_percent <= 0.32


def test_target_fraction_still_fails_closed() -> None:
    decision = decide(all_in_cost=0.28, target_r=1.0)
    assert decision.is_shadow
    assert decision.reason == "COST_CONSUMES_TOO_MUCH_TARGET"
