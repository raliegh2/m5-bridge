from types import SimpleNamespace

from mt5_ai_bridge.v14_13_cost_regime_profile import CostRegimeDecision
from mt5_ai_bridge.v14_16_quality_allocation import quality_risk_target
from mt5_ai_bridge.v14_17_cost_adjusted_consensus import (
    CostAdjustedConsensusController,
    RollingNetEvidence,
    contextual_demotion_authorized,
    currency_exposure,
    live_context_evidence_authorized,
)


def funded_parent(**kwargs):
    return CostRegimeDecision(
        funded=True,
        regime="EXTENDED_COST_V12",
        risk_percent=float(kwargs["base_risk_percent"]),
        reason="parent",
        all_in_cost_r=float(kwargs["all_in_cost"]),
        target_r=float(kwargs["target_r"]),
    )


def candidate(**overrides):
    values = {
        "symbol": "GBPUSD",
        "engine": "GBPUSD_SWING_RETEST",
        "setup": "H4_BREAKOUT_RETEST",
        "mode": "V12",
        "side": "BUY",
        "entry_time": "2026-07-18T12:00:00+00:00",
        "base_risk_percent": 0.15,
        "all_in_cost": 0.02,
        "target_r": 4.0,
        "config": SimpleNamespace(),
    }
    values.update(overrides)
    return values


def closed_trade(value=-0.20):
    return {
        "symbol": "GBPUSD",
        "engine": "GBPUSD_SWING_RETEST",
        "setup": "H4_BREAKOUT_RETEST",
        "engine_group": "V12",
        "side": "BUY",
        "entry_time": "2026-07-01T12:00:00+00:00",
        "cost_regime": "EXTENDED_COST_V12",
        "r_multiple": value,
    }


def test_rolling_evidence_uses_bounded_prior_results():
    evidence = RollingNetEvidence(window=3)
    for value in (1.0, -1.0, -0.5, 0.25):
        evidence.add(value)
    assert evidence.trades == 3
    assert abs(evidence.mean_r - (-1.25 / 3.0)) < 1e-12


def test_contextual_demotion_requires_mature_negative_evidence():
    evidence = RollingNetEvidence()
    for _ in range(19):
        evidence.add(-0.20)
    assert not contextual_demotion_authorized(evidence)
    evidence.add(-0.20)
    assert contextual_demotion_authorized(evidence)


def test_zero_cost_is_exactly_retained():
    controller = CostAdjustedConsensusController(parent_decision=funded_parent)
    for _ in range(30):
        controller.record_closed(closed_trade())
    decision = controller.decision(**candidate(all_in_cost=0.0))
    assert decision.risk_percent == 0.15
    assert decision.regime == "EXTENDED_COST_V12"


def test_prior_closed_negative_v12_direction_is_demoted_and_not_reuplifted():
    controller = CostAdjustedConsensusController(parent_decision=funded_parent)
    for _ in range(20):
        controller.record_closed(closed_trade())
    decision = controller.decision(**candidate())
    assert decision.regime == "REASONING_REDUCED"
    assert abs(decision.risk_percent - 0.075) < 1e-12
    target, reason = quality_risk_target(
        symbol="GBPUSD",
        engine="GBPUSD_V10_PRECISION",
        setup="PRIMARY_16UTC_BREAKOUT",
        mode="V12",
        side="BUY",
        entry_time="2026-07-18T16:00:00+00:00",
        all_in_cost_r=0.02,
        nominal_risk_percent=0.50,
        current_risk_percent=decision.risk_percent,
        current_decision=decision,
        historical_profile_authorized=True,
    )
    assert target is None
    assert reason in {"REDUCED_OR_PROBATION_REGIME", "EXISTING_RISK_REDUCTION_PRESERVED"}


def test_currency_exposure_is_directional():
    assert currency_exposure("GBPUSD", 1, 0.5) == {"GBP": 0.5, "USD": -0.5}
    assert currency_exposure("GBPUSD", -1, 0.5) == {"GBP": -0.5, "USD": 0.5}


def test_live_context_requires_reconciled_mature_samples():
    ok, reason = live_context_evidence_authorized(
        {
            "broker_reconciled": True,
            "direction": {"trades": 30, "mean_r": -0.10, "profit_factor": 0.80},
            "symbol_mode": {"trades": 40},
        }
    )
    assert ok
    assert reason == "LIVE_CONTEXT_BROKER_NET_CONFIRMED"
    denied, denied_reason = live_context_evidence_authorized(
        {
            "broker_reconciled": False,
            "direction": {"trades": 100},
            "symbol_mode": {"trades": 100},
        }
    )
    assert not denied
    assert denied_reason == "LIVE_CONTEXT_NOT_BROKER_RECONCILED"
