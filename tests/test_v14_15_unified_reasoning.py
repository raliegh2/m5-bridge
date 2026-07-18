from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v14_3_live_execution import LiveSignal
from mt5_ai_bridge.v14_3_research_parity_execution import ResearchParityLiveRunnerConfig
from mt5_ai_bridge.v14_14_extended_cost_profile import ExtendedCostRegimeConfig
from mt5_ai_bridge.v14_15_unified_reasoning import (
    DUAL_ENGINE_REGISTRY,
    evidence_multiplier,
    probation_profile,
    unified_cost_reasoning_decision,
    validate_dual_engine_registry,
)
from mt5_ai_bridge.v14_15_unified_reasoning_execution import (
    UnifiedReasoningLiveExecutor,
    UnifiedReasoningState,
)

NOW = datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc)


class FakeBroker:
    ACCOUNT_TRADE_MODE_DEMO = 0
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def account_info(self):
        return SimpleNamespace(
            balance=100000.0,
            equity=100000.0,
            login=123,
            server="UnitTest-Demo",
            trade_mode=0,
        )

    def positions_get(self, **_kwargs):
        return []

    def symbol_info(self, _symbol):
        return SimpleNamespace(
            visible=True,
            point=0.00001,
            digits=5,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=10,
            filling_mode=1,
        )

    def symbol_info_tick(self, _symbol):
        return SimpleNamespace(bid=1.35000, ask=1.35004)

    def history_deals_get(self, *_args, **_kwargs):
        return []


def runner_config(tmp_path) -> ResearchParityLiveRunnerConfig:
    value = ResearchParityLiveRunnerConfig(
        execution_mode="READ_ONLY",
        state_path=str(tmp_path / "state.json"),
        maximum_signal_age_minutes=90,
    )
    value.validate()
    return value


def signal(mode: str = "ICT", side: str = "BUY") -> LiveSignal:
    return LiveSignal(
        symbol="USDJPY",
        broker_symbol="USDJPY",
        engine=(
            "USDJPY_ICT_SESSION_SWEEP"
            if mode == "ICT"
            else "USDJPY_SAFE_HAVEN_BREAKOUT"
        ),
        setup=(
            "usdjpy_ict_session_sweep"
            if mode == "ICT"
            else "D1_H4_40BAR_BREAKOUT"
        ),
        mode=mode,
        side=side,
        signal_time=NOW,
        requested_risk_percent=0.55 if mode == "ICT" else 0.15,
        stop_pips=10.0,
        target_pips=15.0 if mode == "ICT" else 30.0,
        metadata={"timeframe": "H1" if mode == "ICT" else "H4"},
    )


def test_registry_contains_both_modes_for_every_symbol() -> None:
    validate_dual_engine_registry()
    assert set(DUAL_ENGINE_REGISTRY) == {
        "GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"
    }
    assert all(set(modes) == {"V12", "ICT"} for modes in DUAL_ENGINE_REGISTRY.values())


def test_existing_v14_14_funded_trade_is_preserved() -> None:
    decision = unified_cost_reasoning_decision(
        symbol="GBPUSD",
        engine="GBPUSD_V10_PRECISION",
        setup="PRIMARY_16UTC_BREAKOUT",
        mode="V12",
        side="BUY",
        entry_time=NOW,
        base_risk_percent=0.50,
        all_in_cost=0.10,
        target_r=3.0,
        config=ExtendedCostRegimeConfig(),
    )
    assert decision.funded
    assert decision.risk_percent == pytest.approx(0.50)
    assert decision.regime == "EXTENDED_COST_V12"


def test_usdjpy_ict_quality_profile_reopens_shadowed_mode_at_bounded_risk() -> None:
    decision = unified_cost_reasoning_decision(
        symbol="USDJPY",
        engine="USDJPY_ICT_SESSION_SWEEP",
        setup="usdjpy_ict_session_sweep",
        mode="ICT",
        side="SELL",
        entry_time=NOW,
        base_risk_percent=0.55,
        all_in_cost=0.28,
        target_r=1.5,
        config=ExtendedCostRegimeConfig(),
    )
    assert decision.funded
    assert decision.regime == "DUAL_ENGINE_PROBATION"
    assert decision.risk_percent == pytest.approx(0.10)


def test_usdjpy_v12_recovery_is_lower_than_original_risk() -> None:
    decision = unified_cost_reasoning_decision(
        symbol="USDJPY",
        engine="USDJPY_SAFE_HAVEN_BREAKOUT",
        setup="D1_H4_40BAR_BREAKOUT",
        mode="V12",
        side="SELL",
        entry_time=NOW,
        base_risk_percent=0.15,
        all_in_cost=0.10,
        target_r=3.0,
        config=ExtendedCostRegimeConfig(),
    )
    assert decision.funded
    assert decision.risk_percent == pytest.approx(0.05)
    assert decision.risk_percent < 0.15


def test_audusd_hour_10_is_not_reopened() -> None:
    value = probation_profile(
        symbol="AUDUSD",
        engine="AUDUSD_ICT_ASIA_LONDON",
        mode="ICT",
        side="SELL",
        entry_time="2026-07-18T10:00:00+00:00",
    )
    assert value is None


def test_negative_mature_live_evidence_shadows_engine() -> None:
    multiplier, reason = evidence_multiplier(
        [-1.0] * 8,
        [-1.0] * 8,
    )
    assert multiplier == 0.0
    assert reason == "LIVE_EDGE_FAILED"


def test_positive_mature_live_evidence_allows_full_existing_cap() -> None:
    multiplier, reason = evidence_multiplier(
        [0.5, -0.2] * 5,
        [0.4, -0.1] * 5,
    )
    assert multiplier == 1.0
    assert reason == "LIVE_EDGE_CONFIRMED"


def test_state_records_v12_and_ict_engine_results(tmp_path) -> None:
    state = UnifiedReasoningState(str(tmp_path / "state.json"))
    for index, mode in enumerate(("V12", "ICT"), start=1):
        position = {
            "ticket": index,
            "symbol": "USDJPY",
            "engine": "USDJPY_SAFE_HAVEN_BREAKOUT" if mode == "V12" else "USDJPY_ICT_SESSION_SWEEP",
            "setup": "setup",
            "mode": mode,
            "side": "BUY",
            "risk_dollars": 100.0,
        }
        state.data.setdefault("positions", {})[str(index)] = position
        state.record_closed(position, 25.0, NOW)
    assert state.engine_results("USDJPY_SAFE_HAVEN_BREAKOUT") == [0.25]
    assert state.engine_results("USDJPY_ICT_SESSION_SWEEP") == [0.25]
    assert state.symbol_mode_results("USDJPY", "V12") == [0.25]
    assert state.symbol_mode_results("USDJPY", "ICT") == [0.25]


def test_opposite_v12_ict_positions_conflict(tmp_path) -> None:
    executor = UnifiedReasoningLiveExecutor(FakeBroker(), runner_config(tmp_path))
    executor.state.data["positions"] = {
        "1": {
            "symbol": "USDJPY",
            "mode": "V12",
            "side": "SELL",
        }
    }
    compatible, reason = executor._cross_engine_context(signal(mode="ICT", side="BUY"))
    assert not compatible
    assert reason == "CROSS_ENGINE_DIRECTION_CONFLICT"
