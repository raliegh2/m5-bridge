from types import SimpleNamespace

from mt5_ai_bridge.v14_13_cost_regime_profile import CostRegimeDecision
from mt5_ai_bridge.v14_17_cost_adjusted_consensus import (
    CostAdjustedConsensusController,
    RollingNetEvidence,
)
from mt5_ai_bridge.v14_18_hierarchical_regime_meta import (
    HIERARCHICAL_POSITIVE_OVERRIDE_R,
    HierarchicalPosterior,
    HierarchicalRegimeMetaLabeler,
    classify_market_regime,
    hierarchical_posterior,
    live_hierarchy_authorized,
    meta_label_from_evidence,
)


def funded_parent(**kwargs):
    return CostRegimeDecision(
        funded=True,
        regime="EXTENDED_COST_V12" if kwargs["all_in_cost"] > 0 else "ZERO_COST",
        risk_percent=float(kwargs["base_risk_percent"]),
        reason="parent",
        all_in_cost_r=float(kwargs["all_in_cost"]),
        target_r=float(kwargs["target_r"]),
    )


def candidate(**overrides):
    values = {
        "symbol": "EURUSD",
        "engine": "EURUSD_SWING_CORE",
        "setup": "H4_DONCHIAN_BREAKOUT",
        "mode": "V12",
        "side": "BUY",
        "entry_time": "2026-07-18T12:00:00+00:00",
        "base_risk_percent": 0.55,
        "all_in_cost": 0.05,
        "target_r": 3.0,
        "config": SimpleNamespace(),
    }
    values.update(overrides)
    return values


def closed_trade(index: int, value: float = -0.25, mode: str = "V12"):
    return {
        "trade_id": index,
        "symbol": "EURUSD",
        "engine": "EURUSD_SWING_CORE" if mode == "V12" else "EURUSD_ICT_LIQUIDITY",
        "setup": "H4_DONCHIAN_BREAKOUT" if mode == "V12" else "eurusd_ict_liquidity",
        "engine_group": mode,
        "side": "BUY",
        "entry_time": f"2026-06-{(index % 20) + 1:02d}T12:00:00+00:00",
        "cost_regime": "REASONING_REDUCED",
        "r_multiple": value,
    }


def test_market_regime_classifier_is_structural_and_pre_entry():
    assert classify_market_regime(
        mode="ICT",
        engine="EURUSD_ICT_LIQUIDITY",
        setup="eurusd_ict_liquidity",
        consensus="ALIGNED",
        parent_regime="EXTENDED_COST_ICT",
    ) == "RANGE"
    assert classify_market_regime(
        mode="V12",
        engine="GBPUSD_SWING_RETEST",
        setup="H4_BREAKOUT_RETEST",
        consensus="ALIGNED",
        parent_regime="EXTENDED_COST_V12",
    ) == "TRANSITION"
    assert classify_market_regime(
        mode="V12",
        engine="AUDUSD_TREND_PULLBACK",
        setup="D1_H4_EMA_PULLBACK_04_08UTC",
        consensus="ALIGNED",
        parent_regime="EXTENDED_COST_V12",
    ) == "TREND"
    assert classify_market_regime(
        mode="V12",
        engine="AUDUSD_TREND_PULLBACK",
        setup="D1_H4_EMA_PULLBACK_04_08UTC",
        consensus="CONFLICT",
        parent_regime="EXTENDED_COST_V12",
    ) == "TRANSITION"


def test_hierarchical_posterior_shrinks_local_evidence():
    broad = RollingNetEvidence()
    local = RollingNetEvidence()
    for _ in range(40):
        broad.add(0.20)
    for _ in range(5):
        local.add(-1.0)
    posterior = hierarchical_posterior([("broad", broad), ("local", local)])
    assert -1.0 < posterior.score_r < 0.20
    assert posterior.confidence > 0
    assert posterior.effective_trades == 40


def test_strong_positive_hierarchy_can_preserve_full():
    current = CostRegimeDecision(True, "REASONING_REDUCED", 0.25, "parent", 0.05, 3.0)
    posterior = HierarchicalPosterior(
        score_r=HIERARCHICAL_POSITIVE_OVERRIDE_R,
        confidence=1.0,
        mature_negative_nodes=1,
        node_count=8,
        effective_trades=100,
        nodes={},
    )
    meta = meta_label_from_evidence(
        current=current,
        mode="V12",
        all_in_cost_r=0.05,
        market_regime="TREND",
        posterior=posterior,
        direction_evidence={"trades": 100, "mean_r": -0.50, "profit_factor": 0.10},
    )
    assert meta.label == "FULL"


def test_meta_labeler_shadows_only_after_parent_reduction_and_mature_losses():
    parent = CostAdjustedConsensusController(parent_decision=funded_parent)
    meta = HierarchicalRegimeMetaLabeler(parent)
    for index in range(20):
        meta.record_closed(closed_trade(index))
    decision = meta.decision(**candidate())
    assert decision.regime == "SHADOW"
    assert decision.risk_percent == 0.0
    assert meta.events[-1]["v14_18_meta_label"] == "SHADOW"


def test_zero_cost_parity_and_no_uplift_are_preserved():
    parent = CostAdjustedConsensusController(parent_decision=funded_parent)
    meta = HierarchicalRegimeMetaLabeler(parent)
    for index in range(30):
        meta.record_closed(closed_trade(index))
    zero = meta.decision(**candidate(all_in_cost=0.0))
    assert zero.risk_percent == 0.55
    assert meta.events[-1]["v14_18_meta_label"] == "FULL"


def test_ict_range_policy_remains_full_during_stability_phase():
    current = CostRegimeDecision(True, "REASONING_REDUCED", 0.20, "parent", 0.18, 2.0)
    posterior = HierarchicalPosterior(-0.80, 1.0, 8, 8, 100, {})
    meta = meta_label_from_evidence(
        current=current,
        mode="ICT",
        all_in_cost_r=0.18,
        market_regime="RANGE",
        posterior=posterior,
        direction_evidence={"trades": 100, "mean_r": -0.80, "profit_factor": 0.10},
    )
    assert meta.label == "FULL"
    assert meta.multiplier == 1.0


def test_live_hierarchy_requires_reconciled_chronological_maturity():
    ok, reason = live_hierarchy_authorized(
        {
            "broker_reconciled": True,
            "chronological": True,
            "direction": {"trades": 40},
            "engine": {"trades": 50},
            "symbol_mode": {"trades": 60},
        }
    )
    assert ok
    assert reason == "LIVE_HIERARCHY_BROKER_NET_CONFIRMED"
    denied, denied_reason = live_hierarchy_authorized(
        {
            "broker_reconciled": True,
            "chronological": False,
            "direction": {"trades": 100},
            "engine": {"trades": 100},
            "symbol_mode": {"trades": 100},
        }
    )
    assert not denied
    assert denied_reason == "LIVE_HIERARCHY_NOT_CHRONOLOGICAL"
