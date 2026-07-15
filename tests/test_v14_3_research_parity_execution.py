from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v14_3_live_execution import LiveSignal
from mt5_ai_bridge.v14_3_research_parity_execution import (
    PARITY_DRAWDOWN_GOVERNOR,
    PARITY_MAX_COMBINED_OPEN_RISK_PERCENT,
    PARITY_MAX_ICT_OPEN_RISK_PERCENT,
    ResearchParityLiveExecutor,
    ResearchParityLiveRunnerConfig,
)
from v14_3_signals_research_parity import build_live_signals


class FakeClient:
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
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self, trade_mode: int = 0) -> None:
        self.account = SimpleNamespace(
            balance=5000.0,
            equity=5000.0,
            login=12345,
            server="UnitTest-Demo",
            trade_mode=trade_mode,
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
        self.tick = SimpleNamespace(bid=1.10000, ask=1.10010)
        self.positions: list[SimpleNamespace] = []
        self.calls: list[str] = []

    def account_info(self):
        return self.account

    def positions_get(self, **kwargs):
        positions = list(self.positions)
        symbol = kwargs.get("symbol")
        if symbol:
            positions = [position for position in positions if position.symbol == symbol]
        return positions

    def history_deals_get(self, *_args, **_kwargs):
        return []

    def symbol_info(self, _symbol):
        return self.info

    def symbol_info_tick(self, _symbol):
        return self.tick

    def order_calc_profit(self, _order_type, _symbol, volume, price_open, price_close):
        return -abs(price_open - price_close) * 100000.0 * float(volume)

    def order_check(self, _request):
        self.calls.append("check")
        return SimpleNamespace(retcode=0, comment="Done")

    def order_send(self, _request):
        self.calls.append("send")
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=98765,
            deal=98765,
            comment="Done",
        )

    def last_error(self):
        return (0, "OK")


def config(tmp_path, mode="READ_ONLY", **kwargs) -> ResearchParityLiveRunnerConfig:
    values = {
        "execution_mode": mode,
        "state_path": str(tmp_path / "parity-state.json"),
        "forward_gate_passed": False,
        "allow_demo_auto": False,
        "max_deviation_points": 10,
        "maximum_signal_age_minutes": 90,
    }
    values.update(kwargs)
    result = ResearchParityLiveRunnerConfig(**values)
    result.validate()
    return result


def ict_signal(
    symbol="EURUSD",
    setup="eurusd_ict_liquidity",
    engine="EURUSD_ICT_LIQUIDITY",
    hour=12,
    risk=0.55,
) -> LiveSignal:
    return LiveSignal(
        symbol=symbol,
        broker_symbol=symbol,
        engine=engine,
        setup=setup,
        mode="ICT",
        side="BUY",
        signal_time=datetime(2026, 7, 15, hour, 0, tzinfo=timezone.utc),
        requested_risk_percent=risk,
        stop_pips=20.0,
        target_pips=40.0,
        metadata={},
    )


def v12_signal() -> LiveSignal:
    return LiveSignal(
        symbol="EURUSD",
        broker_symbol="EURUSD",
        engine="EURUSD_SWING_CORE",
        setup="H4_DONCHIAN_BREAKOUT",
        mode="V12",
        side="BUY",
        signal_time=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        requested_risk_percent=0.55,
        stop_pips=20.0,
        target_pips=60.0,
        metadata={},
    )


def position(ticket: int, symbol: str = "EURUSD") -> SimpleNamespace:
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        type=FakeClient.POSITION_TYPE_BUY,
        price_open=1.10010,
        price_current=1.10010,
        sl=1.09810,
        tp=1.10410,
        volume=0.10,
        profit=0.0,
        magic=0,
    )


def test_config_is_fixed_to_research_parity_even_with_old_shell_overrides(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V14_3_LIVE_MAX_RISK_PERCENT", "0.10")
    monkeypatch.setenv("V14_3_MAX_OPEN_RISK_PERCENT", "0.10")
    monkeypatch.setenv("V14_3_LIVE_HARD_DD_PERCENT", "6.0")
    result = ResearchParityLiveRunnerConfig.from_env()
    assert result.max_live_risk_percent == 0.80
    assert result.max_open_risk_percent == PARITY_MAX_COMBINED_OPEN_RISK_PERCENT
    assert result.live_hard_drawdown_percent == 9.60


def test_provider_uses_exact_gbp_setup_risk_table(monkeypatch) -> None:
    raw = [{
        "symbol": "GBPUSD",
        "engine": "ICT_V14_3_GBPUSD",
        "setup": "breakout_60_fade",
        "side": "SELL",
        "signal_time": datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        "risk_percent": 0.45,
        "stop_pips": 10.0,
        "target_pips": 12.5,
        "metadata": {},
    }]
    monkeypatch.setattr(
        "v14_3_signals_research_parity.build_detected_signals",
        lambda _client: raw,
    )
    values = build_live_signals(object())
    assert values[0]["risk_percent"] == 0.731
    assert values[0]["metadata"]["setup_risk_percent"] == 0.731


def test_auto_demo_uses_satellite_risk_and_broker_checks(tmp_path) -> None:
    client = FakeClient()
    executor = ResearchParityLiveExecutor(
        client,
        config(
            tmp_path,
            "AUTO",
            forward_gate_passed=True,
            allow_demo_auto=True,
        ),
    )
    result = executor.place(
        ict_signal(),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert result.code == "ORDER_FILLED"
    assert client.calls == ["check", "send"]
    assert result.proposal["admission_risk_percent"] == pytest.approx(0.55)
    assert result.proposal["governed_risk_percent"] == pytest.approx(0.55)
    assert result.proposal["actual_risk_percent"] <= 0.55


def test_auto_remains_demo_only(tmp_path) -> None:
    client = FakeClient(trade_mode=FakeClient.ACCOUNT_TRADE_MODE_REAL)
    executor = ResearchParityLiveExecutor(
        client,
        config(
            tmp_path,
            "AUTO",
            forward_gate_passed=True,
            allow_demo_auto=True,
        ),
    )
    result = executor.place(
        ict_signal(),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert result.code == "DEMO_ACCOUNT_REQUIRED"
    assert client.calls == []


def test_ict_and_combined_admission_caps_match_research(tmp_path) -> None:
    client = FakeClient()
    client.positions = [position(1), position(2)]
    executor = ResearchParityLiveExecutor(client, config(tmp_path))
    executor.state.data["positions"] = {
        "1": {
            "ticket": 1,
            "symbol": "EURUSD",
            "mode": "ICT",
            "admission_risk_percent": 1.40,
        },
        "2": {
            "ticket": 2,
            "symbol": "GBPUSD",
            "mode": "V12",
            "admission_risk_percent": 1.00,
        },
    }
    executor.state.save()
    result = executor.place(
        ict_signal(),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert PARITY_MAX_ICT_OPEN_RISK_PERCENT == 1.75
    assert result.code == "ICT_OPEN_RISK_CAP"

    executor.state.data["positions"]["1"]["admission_risk_percent"] = 1.20
    executor.state.data["positions"]["2"]["admission_risk_percent"] = 1.60
    executor.state.save()
    result = executor.place(
        ict_signal(setup="eurusd_ict_liquidity_2"),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert result.code == "COMBINED_OPEN_RISK_CAP"


def test_loss_pressure_applies_symbol_multiplier(tmp_path) -> None:
    client = FakeClient()
    executor = ResearchParityLiveExecutor(client, config(tmp_path))
    executor.state.reset_day(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc), 5000.0)
    executor.state.data["day"]["global_consecutive_losses"] = 1
    executor.state.save()
    signal = ict_signal(
        symbol="GBPUSD",
        setup="sweep_reclaim_60",
        engine="ICT_V14_3_GBPUSD",
        risk=0.45,
    )
    result = executor.place(
        signal,
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert result.code == "READ_ONLY_PROPOSAL"
    assert result.proposal["admission_risk_percent"] == pytest.approx(0.45 * 0.82)


def test_symbol_session_and_entry_cluster_guards(tmp_path) -> None:
    client = FakeClient()
    executor = ResearchParityLiveExecutor(client, config(tmp_path))
    early_gbpjpy = ict_signal(
        symbol="GBPJPY",
        setup="sweep_reclaim_60",
        engine="ICT_V14_3_GBPJPY",
        hour=6,
        risk=0.33,
    )
    result = executor.place(
        early_gbpjpy,
        now=datetime(2026, 7, 15, 6, 1, tzinfo=timezone.utc),
    )
    assert result.code == "SYMBOL_SESSION_BLOCK"

    executor.state.reset_day(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc), 5000.0)
    executor.state.data["day"]["symbol_entries"]["EURUSD"] = [
        "2026-07-15T11:30:00+00:00"
    ]
    executor.state.save()
    clustered = executor.place(
        ict_signal(),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert clustered.code == "TRADE_CLUSTER_SYMBOL_HOUR"


def test_enhanced_drawdown_governor_applies_to_v12_and_hard_stops(tmp_path) -> None:
    client = FakeClient()
    client.account.equity = 4550.0  # 9.0% below a 5,000 peak.
    executor = ResearchParityLiveExecutor(client, config(tmp_path))
    executor.state.data["peak_equity"] = 5000.0
    executor.state.save()
    result = executor.place(
        v12_signal(),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert PARITY_DRAWDOWN_GOVERNOR.defensive_multiplier == 0.50
    assert result.proposal["governed_risk_percent"] == pytest.approx(0.275)

    other = ResearchParityLiveExecutor(client, config(tmp_path / "hard"))
    other.state.data["peak_equity"] = 5000.0
    other.state.save()
    client.account.equity = 4520.0  # 9.6% drawdown.
    stopped = other.place(
        v12_signal(),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert stopped.code == "DRAWDOWN_GOVERNOR_HARD_STOP"


def test_closed_ict_losses_activate_global_and_symbol_controls(tmp_path) -> None:
    executor = ResearchParityLiveExecutor(FakeClient(), config(tmp_path))
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    executor.state.reset_day(now, 5000.0)
    for ticket in range(1, 7):
        stored = {
            "ticket": ticket,
            "symbol": "GBPUSD",
            "mode": "ICT",
        }
        executor.state.data["positions"][str(ticket)] = stored
        executor.state.record_closed(stored, -10.0, now)
    day = executor.state.data["day"]
    assert day["global_consecutive_losses"] == 6
    assert day["pause_until"] is not None
    assert day["symbol_blocked"]["GBPUSD"] is True
