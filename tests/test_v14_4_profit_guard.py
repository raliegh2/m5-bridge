from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v14_3_live_execution import LiveSignal
from mt5_ai_bridge.v14_3_research_parity_execution import (
    ResearchParityLiveRunnerConfig,
)
from mt5_ai_bridge.v14_4_profit_guard import (
    OBSERVATION_RISK_PERCENT,
    ProfitGuardConfig,
    apply_expectancy_tier,
    expectancy_tier,
    reconstruct_peak_balance,
    spread_cost_reason,
)
from mt5_ai_bridge.v14_4_profit_guard_execution import ProfitGuardedLiveExecutor

NOW = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)


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
        self.tick = SimpleNamespace(bid=1.35350, ask=1.35360)
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
        self.positions = [
            SimpleNamespace(
                ticket=900,
                identifier=900,
                symbol=request["symbol"],
                type=self.POSITION_TYPE_BUY,
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


def _signal(
    signal_time: datetime,
    timeframe: str = "M1",
    stop_pips: float = 5.0,
    setup: str = "sweep_reclaim_60",
) -> LiveSignal:
    return LiveSignal(
        symbol="GBPUSD",
        broker_symbol="GBPUSD",
        engine="ICT_V14_3_GBPUSD",
        setup=setup,
        mode="ICT",
        side="BUY",
        signal_time=signal_time,
        requested_risk_percent=0.45,
        stop_pips=stop_pips,
        target_pips=stop_pips * 1.25,
        metadata={"timeframe": timeframe},
    )


def _executor(tmp_path, client=None, guard=None) -> ProfitGuardedLiveExecutor:
    return ProfitGuardedLiveExecutor(
        client or FakeBrokerClient(),
        _config(tmp_path),
        guard_config=guard or ProfitGuardConfig(),
    )


# ----------------------------------------------------------------------
# Pure guard logic
# ----------------------------------------------------------------------


def test_spread_cost_reason_blocks_expensive_scalps() -> None:
    config = ProfitGuardConfig()
    assert spread_cost_reason(1.1, 5.0, config) is not None
    assert spread_cost_reason(0.4, 5.0, config) is None
    assert spread_cost_reason(0.7, 7.5, config) is None
    # V12 swing stops are large; realistic spreads never trip the gate.
    assert spread_cost_reason(1.5, 60.0, config) is None


def test_expectancy_tiers() -> None:
    config = ProfitGuardConfig()
    # Not enough closed trades: always FULL.
    assert expectancy_tier([-1.0] * 7, config) == "FULL"
    # Mild bleed (nine trades summing to -4.5R): reduced.
    results = [-1.0] * 6 + [1.25] * 2 + [-1.0]
    assert expectancy_tier(results, config) == "REDUCED"
    # Deep bleed: observation only.
    assert expectancy_tier([-1.0] * 10, config) == "OBSERVE"
    # Healthy setup: full risk.
    assert expectancy_tier([1.25, -1.0] * 10, config) == "FULL"


def test_apply_expectancy_tier() -> None:
    config = ProfitGuardConfig()
    assert apply_expectancy_tier(0.45, "FULL", config) == pytest.approx(0.45)
    assert apply_expectancy_tier(0.45, "REDUCED", config) == pytest.approx(0.225)
    assert apply_expectancy_tier(0.45, "OBSERVE", config) == pytest.approx(
        OBSERVATION_RISK_PERCENT
    )


def test_reconstruct_peak_balance_walks_history_backwards() -> None:
    client = FakeBrokerClient()
    base = int(NOW.timestamp())
    # Account lost 2,660 across two closed trades before "now".
    client.deals = [
        SimpleNamespace(time=base - 7200, profit=-1500.0, commission=0.0, swap=0.0, fee=0.0),
        SimpleNamespace(time=base - 3600, profit=-1160.0, commission=0.0, swap=0.0, fee=0.0),
    ]
    peak = reconstruct_peak_balance(client, 97340.0, 30, now=NOW)
    assert peak == pytest.approx(100000.0)


# ----------------------------------------------------------------------
# Executor integration
# ----------------------------------------------------------------------


def test_m1_signal_rejected_when_stale(tmp_path) -> None:
    executor = _executor(tmp_path)
    result = executor.place(_signal(NOW - timedelta(minutes=52)), now=NOW)
    assert result.code == "STALE_M1_SIGNAL"
    # An H1 signal of the same age still follows the shared 90-minute rule.
    result = executor.place(
        _signal(NOW - timedelta(minutes=52), timeframe="H1"),
        now=NOW,
    )
    assert result.code != "STALE_M1_SIGNAL"


def test_spread_gate_rejects_wide_spread_scalp(tmp_path) -> None:
    client = FakeBrokerClient()
    client.tick = SimpleNamespace(bid=1.35350, ask=1.35361)  # 1.1 pips
    executor = _executor(tmp_path, client=client)
    result = executor.place(_signal(NOW - timedelta(minutes=1)), now=NOW)
    assert result.code == "V14_4_SPREAD_COST_GUARD"


def test_daily_loss_stop_blocks_new_entries(tmp_path) -> None:
    client = FakeBrokerClient()
    executor = _executor(tmp_path, client=client)
    executor.state.data["day"] = executor.state._new_day("2026-07-16", 100000.0)
    executor.state.save()
    client.account.equity = 98400.0  # -1.6% on the day
    result = executor.place(_signal(NOW - timedelta(minutes=1)), now=NOW)
    assert result.code == "V14_4_DAILY_LOSS_STOP"


def test_order_fills_when_guards_pass_and_peak_is_seeded(tmp_path) -> None:
    client = FakeBrokerClient()
    client.tick = SimpleNamespace(bid=1.35350, ask=1.35352)  # 0.2 pips
    base = int(NOW.timestamp())
    client.account.balance = 97340.0
    client.account.equity = 97340.0
    client.deals = [
        SimpleNamespace(time=base - 7200, profit=-2660.0, commission=0.0, swap=0.0, fee=0.0),
    ]
    executor = _executor(tmp_path, client=client)
    result = executor.place(_signal(NOW - timedelta(minutes=1)), now=NOW)
    assert result.ok, result.message
    assert result.code == "ORDER_FILLED"
    # Peak was reconstructed from history, not taken from current equity.
    assert executor.state.data["peak_equity"] == pytest.approx(100000.0)
    # Drawdown governor therefore sees the real 2.66% drawdown.
    assert executor.state.drawdown_percent(97340.0) == pytest.approx(2.66)


def test_negative_setup_demoted_to_observation_risk(tmp_path) -> None:
    client = FakeBrokerClient()
    client.tick = SimpleNamespace(bid=1.35350, ask=1.35352)  # 0.2 pips
    executor = _executor(tmp_path, client=client)
    key = "GBPUSD/sweep_reclaim_60"
    executor.state.data["setup_stats"] = {key: [-1.0] * 10}
    executor.state.save()
    result = executor.place(_signal(NOW - timedelta(minutes=1)), now=NOW)
    assert result.ok, result.message
    # 0.025% of 100k = $25 risk; 5-pip stop => 0.5 lots.
    assert result.risk_percent <= OBSERVATION_RISK_PERCENT + 1e-9


def test_record_closed_appends_setup_r(tmp_path) -> None:
    executor = _executor(tmp_path)
    position = {
        "ticket": 900,
        "symbol": "GBPUSD",
        "broker_symbol": "GBPUSD",
        "engine": "ICT_V14_3_GBPUSD",
        "setup": "sweep_reclaim_60",
        "mode": "ICT",
        "side": "BUY",
        "risk_dollars": 450.0,
        "admission_risk_percent": 0.45,
        "executed_risk_percent": 0.45,
        "opened_at": NOW.isoformat(),
    }
    executor.state.data["positions"]["900"] = dict(position)
    executor.state.record_closed(dict(position), -450.0, NOW)
    assert executor.state.setup_results("GBPUSD", "sweep_reclaim_60") == [-1.0]


def test_guard_config_validation() -> None:
    with pytest.raises(ValueError):
        ProfitGuardConfig(daily_loss_stop_percent=12.0).validate()
    with pytest.raises(ValueError):
        ProfitGuardConfig(max_spread_fraction_of_stop=0.0).validate()
    with pytest.raises(ValueError):
        ProfitGuardConfig(
            reduce_threshold_r=-8.0,
            observe_threshold_r=-4.0,
        ).validate()


def test_profit_guard_snapshot(tmp_path) -> None:
    executor = _executor(tmp_path)
    executor.state.data["setup_stats"] = {"GBPUSD/sweep_reclaim_60": [-1.0] * 10}
    snapshot = executor.profit_guard_snapshot()
    assert snapshot["setup_tiers"]["GBPUSD/sweep_reclaim_60"] == "OBSERVE"
    assert snapshot["config"]["daily_loss_stop_percent"] == pytest.approx(1.5)
