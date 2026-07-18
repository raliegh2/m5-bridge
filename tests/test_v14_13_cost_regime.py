from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v14_3_live_execution import LiveSignal
from mt5_ai_bridge.v14_3_research_parity_execution import (
    ResearchParityLiveRunnerConfig,
)
from mt5_ai_bridge.v14_13_cost_regime_execution import CostRegimeLiveExecutor
from mt5_ai_bridge.v14_13_cost_regime_profile import (
    CostRegimeConfig,
    all_in_cost_r,
    cost_regime_decision,
    strict_retail_profile,
)

NOW = datetime(2026, 7, 17, 12, 1, tzinfo=timezone.utc)


class FakeBroker:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self, spread_pips: float = 0.4) -> None:
        self.account = SimpleNamespace(
            balance=100000.0,
            equity=100000.0,
            login=12345,
            server="UnitTest-Demo",
            trade_mode=0,
        )
        self.info = SimpleNamespace(
            visible=True,
            point=0.00001,
            digits=5,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=10,
            filling_mode=1,
        )
        self.tick = SimpleNamespace(
            bid=1.35000,
            ask=1.35000 + spread_pips * 0.0001,
        )

    def account_info(self):
        return self.account

    def positions_get(self, **_kwargs):
        return []

    def symbol_info(self, _symbol):
        return self.info

    def symbol_info_tick(self, _symbol):
        return self.tick

    def order_calc_profit(self, _order_type, _symbol, volume, open_price, close_price):
        return -abs(float(open_price) - float(close_price)) * 100000.0 * float(volume)

    def history_deals_get(self, *_args, **_kwargs):
        return []

    def last_error(self):
        return (0, "OK")


def config(tmp_path) -> ResearchParityLiveRunnerConfig:
    value = ResearchParityLiveRunnerConfig(
        execution_mode="READ_ONLY",
        state_path=str(tmp_path / "state.json"),
        maximum_signal_age_minutes=90,
    )
    value.validate()
    return value


def ict_signal(
    setup: str,
    side: str = "SELL",
    hour: int = 12,
) -> LiveSignal:
    return LiveSignal(
        symbol="GBPUSD",
        broker_symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup=setup,
        mode="ICT",
        side=side,
        signal_time=datetime(2026, 7, 17, hour, 0, tzinfo=timezone.utc),
        requested_risk_percent=0.45,
        stop_pips=5.0,
        target_pips=6.25,
        metadata={"timeframe": "M1"},
    )


def test_all_in_cost_r_matches_short_stop_economics() -> None:
    value = all_in_cost_r(0.4, 5.0, "M1", CostRegimeConfig())
    assert value == pytest.approx(0.13)


def test_zero_cost_keeps_exact_v14_3_risk() -> None:
    decision = cost_regime_decision(
        symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup="sweep_reclaim_60",
        mode="ICT",
        side="BUY",
        entry_time=NOW,
        base_risk_percent=0.45,
        all_in_cost=0.0,
        target_r=1.25,
        config=CostRegimeConfig(),
    )
    assert decision.funded
    assert decision.risk_percent == pytest.approx(0.45)
    assert decision.regime == "ZERO_COST_PARITY"


def test_strict_profiles_use_only_pre_entry_fields() -> None:
    assert strict_retail_profile(
        "GBPUSD", "breakout_15_fade", "SELL", "2026-07-17T12:00:00Z"
    )
    assert strict_retail_profile(
        "GBPJPY", "sweep_reclaim_15", "BUY", "2026-07-17T09:00:00Z"
    )
    assert not strict_retail_profile(
        "GBPUSD", "breakout_30_fade", "SELL", "2026-07-17T12:00:00Z"
    )


def test_high_cost_noncore_is_shadow_not_higher_risk() -> None:
    decision = cost_regime_decision(
        symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup="breakout_30_fade",
        mode="ICT",
        side="SELL",
        entry_time=NOW,
        base_risk_percent=0.32,
        all_in_cost=0.13,
        target_r=1.25,
        config=CostRegimeConfig(),
    )
    assert decision.is_shadow
    assert decision.risk_percent == 0.0


def test_weak_v12_is_shadow_when_cost_is_nonzero() -> None:
    decision = cost_regime_decision(
        symbol="USDJPY",
        engine="USDJPY_SAFE_HAVEN_BREAKOUT",
        setup="D1_H4_40BAR_BREAKOUT",
        mode="V12",
        side="BUY",
        entry_time=NOW,
        base_risk_percent=0.15,
        all_in_cost=0.03,
        target_r=3.0,
        config=CostRegimeConfig(),
    )
    assert decision.is_shadow
    assert decision.reason == "WEAK_V12_AFTER_COSTS"


def test_supported_decision_never_exceeds_frozen_base() -> None:
    decision = cost_regime_decision(
        symbol="GBPJPY",
        engine="ICT_V14_3_GBPJPY",
        setup="sweep_reclaim_15",
        mode="ICT",
        side="BUY",
        entry_time=datetime(2026, 7, 17, 9, tzinfo=timezone.utc),
        base_risk_percent=0.735,
        all_in_cost=0.18,
        target_r=1.25,
        config=CostRegimeConfig(),
    )
    assert decision.funded
    assert decision.risk_percent <= 0.735


def test_live_executor_funds_strict_retail_profile(tmp_path) -> None:
    executor = CostRegimeLiveExecutor(FakeBroker(0.4), config(tmp_path))
    result = executor.place(ict_signal("breakout_15_fade"), now=NOW)
    assert result.ok, result.message
    assert result.code == "READ_ONLY_PROPOSAL"
    assert result.risk_percent > 0


def test_live_executor_shadows_noncore_high_cost_trade(tmp_path) -> None:
    executor = CostRegimeLiveExecutor(FakeBroker(0.4), config(tmp_path))
    result = executor.place(ict_signal("breakout_30_fade"), now=NOW)
    assert not result.ok
    assert result.code == "V14_13_COST_REGIME_SHADOW"
    assert result.risk_percent == 0


def test_config_rejects_inverted_thresholds() -> None:
    with pytest.raises(ValueError):
        CostRegimeConfig(parity_cost_r=0.10, medium_cost_r=0.09).validate()
