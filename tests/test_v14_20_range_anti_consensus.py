from __future__ import annotations

import pandas as pd

from mt5_ai_bridge.v14_13_cost_regime_profile import CostRegimeDecision
from mt5_ai_bridge.v14_17_cost_adjusted_consensus import RollingNetEvidence
from mt5_ai_bridge.v14_20_range_anti_consensus import (
    RangeAntiConsensusController,
    RangeContext,
    RangeSignalIndex,
    conflict_shadow_authorized,
    live_conflict_shadow_authorized,
)


def funded_decision(risk: float = 0.15) -> CostRegimeDecision:
    return CostRegimeDecision(
        funded=True,
        regime="REASONING_REDUCED",
        risk_percent=risk,
        reason="parent",
        all_in_cost_r=0.03,
        target_r=2.0,
    )


def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "engine": "V14_19_D1_RANGE_REVERSION_SHADOW",
                "symbol": "GBPUSD",
                "side": "BUY",
                "signal_available_at": "2020-01-02T00:00:00Z",
                "entry_time": "2020-01-02T08:00:00Z",
                "exit_time": "2020-01-03T08:00:00Z",
            }
        ]
    )


class DummyParent:
    def __init__(self) -> None:
        self.events = []
        self.closed = []

    def decision(self, **kwargs):
        self.events.append(
            {
                "v14_18_market_regime": "TREND",
                "v14_18_meta_label": "FULL",
            }
        )
        return funded_decision()

    def record_closed(self, item):
        self.closed.append(dict(item))

    def summary(self):
        return {"dummy": True}


def test_range_signal_index_relations_are_pre_entry_only():
    index = RangeSignalIndex(source_frame())
    conflict = index.context(
        symbol="GBPUSD",
        side="SELL",
        entry_time="2020-01-02T12:00:00Z",
    )
    aligned = index.context(
        symbol="GBPUSD",
        side="BUY",
        entry_time="2020-01-02T12:00:00Z",
    )
    unavailable = index.context(
        symbol="GBPUSD",
        side="BUY",
        entry_time="2020-01-02T04:00:00Z",
    )
    assert conflict.relation == "CONFLICT"
    assert aligned.relation == "ALIGNED"
    assert unavailable.relation == "UNAVAILABLE"


def test_conflict_shadow_requires_mature_negative_engine_evidence():
    evidence = RollingNetEvidence(window=20)
    for value in (-1.0,) * 9:
        evidence.add(value)
    authorized, reason = conflict_shadow_authorized(
        current=funded_decision(),
        all_in_cost_r=0.03,
        context=RangeContext("CONFLICT"),
        evidence=evidence,
    )
    assert authorized is False
    assert reason == "ENGINE_CONFLICT_SAMPLE_BELOW_10"

    evidence.add(-1.0)
    authorized, reason = conflict_shadow_authorized(
        current=funded_decision(),
        all_in_cost_r=0.03,
        context=RangeContext("CONFLICT"),
        evidence=evidence,
    )
    assert authorized is True
    assert reason == "MATURE_NEGATIVE_ENGINE_CONFLICT_CONTEXT"


def test_zero_cost_and_non_conflict_contexts_remain_unchanged():
    evidence = RollingNetEvidence(window=20)
    for value in (-1.0,) * 10:
        evidence.add(value)
    zero, zero_reason = conflict_shadow_authorized(
        current=funded_decision(),
        all_in_cost_r=0.0,
        context=RangeContext("CONFLICT"),
        evidence=evidence,
    )
    aligned, aligned_reason = conflict_shadow_authorized(
        current=funded_decision(),
        all_in_cost_r=0.03,
        context=RangeContext("ALIGNED"),
        evidence=evidence,
    )
    assert zero is False
    assert zero_reason == "ZERO_COST_PARITY_UNCHANGED"
    assert aligned is False
    assert aligned_reason == "RANGE_ALIGNED"


def test_controller_shadows_only_after_prior_executed_conflict_closes():
    parent = DummyParent()
    controller = RangeAntiConsensusController(parent, source_frame())

    first = controller.decision(
        symbol="GBPUSD",
        engine="GBPUSD_SWING_RETEST",
        setup="H4_BREAKOUT_RETEST",
        mode="V12",
        side="SELL",
        entry_time="2020-01-02T12:00:00Z",
        all_in_cost=0.03,
    )
    assert first.funded is True
    assert controller.events[-1]["v14_20_action"] == "UNCHANGED"

    item = {
        "entry_time": "2020-01-02T12:00:00Z",
        "symbol": "GBPUSD",
        "engine": "GBPUSD_SWING_RETEST",
        "setup": "H4_BREAKOUT_RETEST",
        "side": "SELL",
        "engine_group": "V12",
        "r_multiple": -1.0,
    }
    controller.record_closed(item)
    assert parent.closed
    assert controller._engine_evidence("GBPUSD_SWING_RETEST").trades == 1

    for _ in range(9):
        controller._engine_evidence("GBPUSD_SWING_RETEST").add(-1.0)
    second = controller.decision(
        symbol="GBPUSD",
        engine="GBPUSD_SWING_RETEST",
        setup="H4_BREAKOUT_RETEST",
        mode="V12",
        side="SELL",
        entry_time="2020-01-02T16:00:00Z",
        all_in_cost=0.03,
    )
    assert second.funded is False
    assert second.risk_percent == 0.0
    assert controller.events[-1]["v14_20_action"] == "SHADOW"
    assert controller.events[-1]["v14_20_engine_conflict_trades"] == 10


def test_live_boundary_requires_feed_parity_and_twenty_reconciled_trades():
    payload = {
        "broker_reconciled": True,
        "chronological": True,
        "range_feed_parity": True,
        "relation": "CONFLICT",
        "trades": 20,
        "mean_r": -0.20,
        "profit_factor": 0.70,
    }
    assert live_conflict_shadow_authorized(payload) == (
        True,
        "LIVE_RANGE_ANTI_CONSENSUS_CONFIRMED",
    )
    payload["range_feed_parity"] = False
    assert live_conflict_shadow_authorized(payload)[0] is False
