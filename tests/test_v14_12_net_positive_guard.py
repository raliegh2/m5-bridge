from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v14_3_live_execution import LiveSignal
from mt5_ai_bridge.v14_3_research_parity_execution import (
    ResearchParityLiveRunnerConfig,
)
from mt5_ai_bridge.v14_5_2_profit_filter_profile import (
    V14_5_2_OBSERVATION_RISK_PERCENT,
    v14_5_2_filter_reason,
    v14_5_2_risk_percent,
)
from mt5_ai_bridge.v14_12_live_execution import (
    NetPositiveLiveExecutor,
    NetPositiveState,
)
from mt5_ai_bridge.v14_12_net_positive_guard import (
    NetPositiveGuardConfig,
    all_in_cost_reason,
    apply_net_positive_tier,
    net_positive_tier,
    rolling_performance,
)

NOW = datetime(2026, 7, 18, 14, 0, tzinfo=timezone.utc)


class FakeBrokerClient:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ACCOUNT_TRADE_MODE_DEMO = 0
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_INOUT = 2
    DEAL_ENTRY_OUT_BY = 3

    def __init__(self) -> None:
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
        self.tick = SimpleNamespace(bid=1.35350, ask=1.35352)
        self.positions: list[SimpleNamespace] = []
        self.deals: list[SimpleNamespace] = []
        self.sent_request = None

    def account_info(self):
        return self.account

    def positions_get(self, **kwargs):
        values = list(self.positions)
        symbol = kwargs.get("symbol")
        if symbol:
            values = [position for position in values if position.symbol == symbol]
        return values

    def symbol_info(self, _symbol):
        return self.info

    def symbol_info_tick(self, _symbol):
        return self.tick

    def order_calc_profit(self, _order_type, _symbol, volume, price_open, price_close):
        return -abs(price_open - price_close) * 100000.0 * float(volume)

    def order_check(self, _request):
        return SimpleNamespace(retcode=0, comment="Done")

    def order_send(self, request):
        self.sent_request = dict(request)
        position_type = (
            self.POSITION_TYPE_BUY
            if request["type"] == self.ORDER_TYPE_BUY
            else self.POSITION_TYPE_SELL
        )
        self.positions = [
            SimpleNamespace(
                ticket=900,
                identifier=900,
                symbol=request["symbol"],
                type=position_type,
                volume=request["volume"],
                price_open=request["price"],
                price_current=request["price"],
                sl=request["sl"],
                tp=request["tp"],
                profit=0.0,
                magic=request["magic"],
                time=1_752_600_060,
                time_msc=1_752_600_060_000,
            )
        ]
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=900,
            deal=901,
            comment="Done",
        )

    def history_deals_get(self, *_args, **_kwargs):
        return list(self.deals)

    def last_error(self):
        return (0, "OK")


def _config(tmp_path) -> ResearchParityLiveRunnerConfig:
    config = ResearchParityLiveRunnerConfig(
        execution_mode="AUTO",
        state_path=str(tmp_path / "state.json"),
        forward_gate_passed=True,
        allow_demo_auto=True,
        maximum_signal_age_minutes=90,
    )
    config.validate()
    return config


def _v12_signal(
    engine: str = "GBPUSD_V10_PRECISION",
    setup: str = "PRIMARY_16UTC_BREAKOUT",
    signal_time: datetime = NOW,
    stop_pips: float = 60.0,
    target_pips: float = 180.0,
) -> LiveSignal:
    return LiveSignal(
        symbol="GBPUSD",
        broker_symbol="GBPUSD",
        engine=engine,
        setup=setup,
        mode="V12",
        side="BUY",
        signal_time=signal_time,
        requested_risk_percent=0.75,
        stop_pips=stop_pips,
        target_pips=target_pips,
        metadata={"timeframe": "H4"},
    )


def _ict_signal(signal_time: datetime = NOW) -> LiveSignal:
    return LiveSignal(
        symbol="GBPUSD",
        broker_symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup="sweep_reclaim_60",
        mode="ICT",
        side="BUY",
        signal_time=signal_time,
        requested_risk_percent=0.45,
        stop_pips=5.0,
        target_pips=6.25,
        metadata={"timeframe": "M1"},
    )


def _executor(tmp_path, client=None, net_guard=None) -> NetPositiveLiveExecutor:
    return NetPositiveLiveExecutor(
        client or FakeBrokerClient(),
        _config(tmp_path),
        net_guard_config=net_guard or NetPositiveGuardConfig(),
    )


# ----------------------------------------------------------------------
# Static V14.5.2 allocation
# ----------------------------------------------------------------------


def test_v14_5_2_cost_robust_static_allocation() -> None:
    monday_12 = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    tuesday_12 = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    monday_16 = datetime(2026, 7, 20, 16, tzinfo=timezone.utc)

    assert v14_5_2_risk_percent("GBPUSD_V10_PRECISION", "V12", monday_12) == pytest.approx(0.75)
    assert v14_5_2_risk_percent("GBPJPY_SWING_CORE", "V12", tuesday_12) == pytest.approx(
        V14_5_2_OBSERVATION_RISK_PERCENT
    )
    assert v14_5_2_risk_percent("EURUSD_SWING_CORE", "V12", monday_16) == pytest.approx(
        V14_5_2_OBSERVATION_RISK_PERCENT
    )
    assert v14_5_2_risk_percent("ICT_V14_3_GBPUSD", "ICT", monday_12) == pytest.approx(
        V14_5_2_OBSERVATION_RISK_PERCENT
    )
    assert v14_5_2_filter_reason("GBPJPY_SWING_CORE", tuesday_12) == "GBPJPY_TUESDAY_OBSERVATION"
    assert v14_5_2_filter_reason("EURUSD_SWING_CORE", monday_16) == "EURUSD_16UTC_OBSERVATION"


# ----------------------------------------------------------------------
# Pure net-positive tier and cost logic
# ----------------------------------------------------------------------


def test_rolling_performance_includes_profit_factor_and_expectancy() -> None:
    result = rolling_performance([1.0, -0.5, 1.0, -0.5], 20)
    assert result.trades == 4
    assert result.net_r == pytest.approx(1.0)
    assert result.expectancy_r == pytest.approx(0.25)
    assert result.profit_factor == pytest.approx(2.0)


def test_tier_is_probation_until_setup_and_symbol_have_enough_net_trades() -> None:
    config = NetPositiveGuardConfig()
    assert net_positive_tier([1.0] * 11, [1.0] * 30, config) == "PROBATION"
    assert net_positive_tier([1.0] * 20, [1.0] * 19, config) == "PROBATION"


def test_tier_observes_when_setup_or_symbol_is_not_profitable_after_costs() -> None:
    config = NetPositiveGuardConfig()
    healthy_setup = [1.0, -0.5] * 8
    healthy_symbol = [1.0, -0.5] * 12
    assert net_positive_tier([-1.0] * 12, healthy_symbol, config) == "OBSERVE"
    assert net_positive_tier(healthy_setup, [-1.0] * 20, config) == "OBSERVE"


def test_tier_reduced_for_positive_but_not_full_strength_evidence() -> None:
    config = NetPositiveGuardConfig(
        full_setup_net_r=8.0,
        full_symbol_net_r=10.0,
    )
    setup = [0.40, -0.25] * 6
    symbol = [0.40, -0.25] * 10
    assert net_positive_tier(setup, symbol, config) == "REDUCED"


def test_tier_full_only_when_setup_and_symbol_are_strong_after_costs() -> None:
    config = NetPositiveGuardConfig()
    setup = [1.0, -0.5] * 6
    symbol = [1.0, -0.5] * 10
    assert net_positive_tier(setup, symbol, config) == "FULL"


def test_risk_application_never_exceeds_static_cost_robust_allocation() -> None:
    config = NetPositiveGuardConfig()
    base = 0.75
    assert apply_net_positive_tier(base, "PROBATION", config) == pytest.approx(0.1875)
    assert apply_net_positive_tier(base, "OBSERVE", config) == pytest.approx(0.025)
    assert apply_net_positive_tier(base, "REDUCED", config) == pytest.approx(0.375)
    assert apply_net_positive_tier(base, "FULL", config) == pytest.approx(0.75)


def test_all_in_cost_gate_blocks_cost_broken_scalp_but_allows_wide_swing() -> None:
    config = NetPositiveGuardConfig()
    # 0.4 spread + 0.25 reserve = 0.65 pips: 13% of a 5-pip stop.
    assert all_in_cost_reason(0.4, 5.0, 6.25, config) is not None
    assert all_in_cost_reason(0.2, 60.0, 180.0, config) is None


# ----------------------------------------------------------------------
# State and executor integration
# ----------------------------------------------------------------------


def test_state_records_net_setup_and_symbol_r_for_all_modes(tmp_path) -> None:
    state = NetPositiveState(tmp_path / "state.json")
    v12 = {
        "ticket": 1,
        "symbol": "GBPUSD",
        "setup": "PRIMARY_16UTC_BREAKOUT",
        "mode": "V12",
        "risk_dollars": 100.0,
    }
    ict = {
        "ticket": 2,
        "symbol": "GBPUSD",
        "setup": "sweep_reclaim_60",
        "mode": "ICT",
        "risk_dollars": 100.0,
    }
    state.data["positions"] = {"1": dict(v12), "2": dict(ict)}
    # P/L values are expected to be broker-net after profit, commission, swap,
    # and fee reconciliation.
    state.record_closed(dict(v12), 50.0, NOW)
    state.record_closed(dict(ict), -25.0, NOW)
    assert state.setup_results("GBPUSD", "PRIMARY_16UTC_BREAKOUT") == [0.5]
    assert state.setup_results("GBPUSD", "sweep_reclaim_60") == [-0.25]
    assert state.symbol_results("GBPUSD") == [0.5, -0.25]


def test_promoted_v12_starts_at_probation_risk(tmp_path) -> None:
    executor = _executor(tmp_path)
    result = executor.place(_v12_signal(), now=NOW)
    assert result.ok, result.message
    assert result.code == "ORDER_FILLED"
    assert result.risk_percent == pytest.approx(0.1875)


def test_promoted_v12_earns_full_risk_only_after_setup_and_symbol_pass(tmp_path) -> None:
    executor = _executor(tmp_path)
    setup_key = "GBPUSD/PRIMARY_16UTC_BREAKOUT"
    executor.state.data["setup_stats"] = {setup_key: [1.0, -0.5] * 6}
    executor.state.data["symbol_stats"] = {"GBPUSD": [1.0, -0.5] * 10}
    executor.state.save()
    result = executor.place(_v12_signal(), now=NOW)
    assert result.ok, result.message
    assert result.risk_percent == pytest.approx(0.75)


def test_negative_symbol_demotes_healthy_setup_to_observation(tmp_path) -> None:
    executor = _executor(tmp_path)
    setup_key = "GBPUSD/PRIMARY_16UTC_BREAKOUT"
    executor.state.data["setup_stats"] = {setup_key: [1.0, -0.5] * 6}
    executor.state.data["symbol_stats"] = {"GBPUSD": [-1.0] * 20}
    executor.state.save()
    result = executor.place(_v12_signal(), now=NOW)
    assert result.ok, result.message
    assert result.risk_percent <= V14_5_2_OBSERVATION_RISK_PERCENT + 1e-9


def test_ict_remains_observation_risk_even_with_strong_history(tmp_path) -> None:
    executor = _executor(tmp_path)
    executor.state.data["setup_stats"] = {
        "GBPUSD/sweep_reclaim_60": [1.25, -1.0] * 10
    }
    executor.state.data["symbol_stats"] = {"GBPUSD": [1.25, -1.0] * 20}
    executor.state.save()
    result = executor.place(_ict_signal(), now=NOW)
    assert result.ok, result.message
    assert result.risk_percent <= V14_5_2_OBSERVATION_RISK_PERCENT + 1e-9


def test_executor_rejects_trade_when_all_in_cost_consumes_edge(tmp_path) -> None:
    client = FakeBrokerClient()
    client.tick = SimpleNamespace(bid=1.35350, ask=1.35354)  # 0.4 pips
    executor = _executor(tmp_path, client=client)
    result = executor.place(_ict_signal(), now=NOW)
    assert result.code == "V14_12_ALL_IN_COST_GUARD"
    assert client.sent_request is None


def test_snapshot_exposes_after_cost_setup_and_symbol_metrics(tmp_path) -> None:
    executor = _executor(tmp_path)
    executor.state.data["setup_stats"] = {
        "GBPUSD/PRIMARY_16UTC_BREAKOUT": [1.0, -0.5] * 6
    }
    executor.state.data["symbol_stats"] = {"GBPUSD": [1.0, -0.5] * 10}
    snapshot = executor.net_positive_snapshot()
    assert snapshot["setup_performance"]["GBPUSD/PRIMARY_16UTC_BREAKOUT"]["tier"] == "FULL"
    assert snapshot["symbol_performance"]["GBPUSD"]["profit_factor"] == pytest.approx(2.0)


def test_net_positive_config_validation() -> None:
    with pytest.raises(ValueError):
        NetPositiveGuardConfig(probation_risk_multiplier=0.75, reduced_risk_multiplier=0.5).validate()
    with pytest.raises(ValueError):
        NetPositiveGuardConfig(maximum_all_in_cost_fraction_of_stop=0.0).validate()
