from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v14_3_live_execution import LiveSignal
from mt5_ai_bridge.v14_21_demo_auto_execution import (
    V1421DemoAutoConfig,
    V1421DemoAutoExecutor,
    validate_demo_runtime,
)


NOW = datetime(2026, 7, 19, 14, 0, tzinfo=timezone.utc)


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
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_INOUT = 2
    DEAL_ENTRY_OUT_BY = 3

    def __init__(self, *, trade_mode: int = 0) -> None:
        self.account = SimpleNamespace(
            balance=5000.0,
            equity=5000.0,
            login=12345,
            server="UnitTest-Demo",
            trade_mode=trade_mode,
            trade_allowed=True,
            trade_expert=True,
        )
        self.terminal = SimpleNamespace(
            connected=True,
            trade_allowed=True,
            tradeapi_disabled=False,
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
            bid=1.10000,
            ask=1.10010,
            time=int(NOW.timestamp()),
            time_msc=int(NOW.timestamp() * 1000),
        )
        self.positions: list[SimpleNamespace] = []
        self.calls: list[str] = []

    def terminal_info(self):
        return self.terminal

    def account_info(self):
        return self.account

    def positions_get(self, **kwargs):
        values = list(self.positions)
        symbol = kwargs.get("symbol")
        if symbol:
            values = [item for item in values if item.symbol == symbol]
        return values

    def history_deals_get(self, *_args, **_kwargs):
        return []

    def symbol_info(self, _symbol):
        return self.info

    def symbol_info_tick(self, _symbol):
        return self.tick

    def order_calc_profit(
        self,
        _order_type,
        _symbol,
        volume,
        price_open,
        price_close,
    ):
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


def make_config(tmp_path, mode="READ_ONLY", **overrides):
    values = {
        "execution_mode": mode,
        "state_path": str(tmp_path / "state.json"),
        "audit_log_path": str(tmp_path / "audit.jsonl"),
        "kill_switch_path": str(tmp_path / "STOP"),
        "forward_gate_passed": mode == "AUTO",
        "allow_demo_auto": mode == "AUTO",
        "expected_login": 12345 if mode == "AUTO" else None,
        "expected_server": "UnitTest-Demo" if mode == "AUTO" else None,
        "demo_acknowledgement": "DEMO_ONLY" if mode == "AUTO" else "",
    }
    values.update(overrides)
    result = V1421DemoAutoConfig(**values)
    result.validate()
    return result


def signal(**overrides) -> LiveSignal:
    values = {
        "symbol": "EURUSD",
        "broker_symbol": "EURUSD",
        "engine": "EURUSD_SWING_CORE",
        "setup": "H4_DONCHIAN_BREAKOUT",
        "mode": "V12",
        "side": "BUY",
        "signal_time": NOW,
        "requested_risk_percent": 0.55,
        "stop_pips": 20.0,
        "target_pips": 60.0,
        "metadata": {"timeframe": "H1"},
    }
    values.update(overrides)
    return LiveSignal(**values)


def test_demo_auto_environment_requires_every_explicit_gate(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V14_21_EXECUTION_MODE", "DEMO_AUTO")
    monkeypatch.setenv("V14_21_FORWARD_GATE_PASSED", "true")
    monkeypatch.setenv("V14_21_ALLOW_DEMO_AUTO", "true")
    monkeypatch.setenv("V14_21_ACKNOWLEDGE_DEMO_ONLY", "DEMO_ONLY")
    monkeypatch.setenv("V14_21_EXPECTED_LOGIN", "12345")
    monkeypatch.setenv("V14_21_EXPECTED_SERVER", "UnitTest-Demo")
    result = V1421DemoAutoConfig.from_env()
    assert result.execution_mode == "AUTO"
    assert result.requested_mode == "DEMO_AUTO"
    assert result.max_live_risk_percent == 0.80
    assert result.max_open_risk_percent == 3.25


def test_demo_auto_config_rejects_missing_acknowledgement(tmp_path) -> None:
    result = V1421DemoAutoConfig(
        execution_mode="AUTO",
        state_path=str(tmp_path / "state.json"),
        forward_gate_passed=True,
        allow_demo_auto=True,
        expected_login=12345,
        expected_server="UnitTest-Demo",
    )
    with pytest.raises(ValueError, match="ACKNOWLEDGE_DEMO_ONLY"):
        result.validate()


def test_real_account_is_rejected_before_any_broker_order(tmp_path) -> None:
    client = FakeClient(trade_mode=FakeClient.ACCOUNT_TRADE_MODE_REAL)
    executor = V1421DemoAutoExecutor(
        client,
        make_config(tmp_path, "AUTO"),
    )
    result = executor.place(signal(), now=NOW)
    assert result.code == "DEMO_ACCOUNT_REQUIRED"
    assert client.calls == []


def test_terminal_and_account_permissions_fail_closed(tmp_path) -> None:
    client = FakeClient()
    config = make_config(tmp_path, "AUTO")
    client.terminal.trade_allowed = False
    status = validate_demo_runtime(client, config)
    assert status.code == "TERMINAL_TRADING_DISABLED"

    client.terminal.trade_allowed = True
    client.account.trade_expert = False
    status = validate_demo_runtime(client, config)
    assert status.code == "ACCOUNT_EXPERT_TRADING_DISABLED"


def test_expected_login_and_server_are_pinned(tmp_path) -> None:
    client = FakeClient()
    config = make_config(tmp_path, "AUTO")
    client.account.login = 99999
    assert validate_demo_runtime(client, config).code == "EXPECTED_LOGIN_MISMATCH"
    client.account.login = 12345
    client.account.server = "Another-Demo"
    assert validate_demo_runtime(client, config).code == "EXPECTED_SERVER_MISMATCH"


def test_kill_switch_blocks_before_order_check(tmp_path) -> None:
    client = FakeClient()
    config = make_config(tmp_path, "AUTO")
    path = config.kill_switch_path
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("STOP\n")
    result = V1421DemoAutoExecutor(client, config).place(
        signal(),
        now=NOW,
    )
    assert result.code == "V14_21_KILL_SWITCH"
    assert client.calls == []


def test_direct_range_engine_remains_shadow_only(tmp_path) -> None:
    client = FakeClient()
    executor = V1421DemoAutoExecutor(
        client,
        make_config(tmp_path),
    )
    result = executor.place(
        signal(engine="V14_19_D1_RANGE_REVERSION_SHADOW"),
        now=NOW,
    )
    assert result.code == "V14_19_RANGE_SHADOW_ONLY"
    assert client.calls == []


def test_mature_live_v14_20_conflict_is_shadowed(tmp_path) -> None:
    client = FakeClient()
    executor = V1421DemoAutoExecutor(
        client,
        make_config(tmp_path),
    )
    candidate = signal(
        metadata={
            "timeframe": "H1",
            "all_in_cost_r": 0.03,
            "v14_20_range_anti_consensus": {
                "broker_reconciled": True,
                "chronological": True,
                "range_feed_parity": True,
                "relation": "CONFLICT",
                "trades": 20,
                "mean_r": -0.20,
                "profit_factor": 0.60,
            },
        }
    )
    result = executor.place(candidate, now=NOW)
    assert result.code == "V14_20_RANGE_CONFLICT_SHADOW"
    assert result.risk_percent == 0.0
    assert client.calls == []


def test_dollar_and_consecutive_loss_stops(tmp_path) -> None:
    client = FakeClient()
    executor = V1421DemoAutoExecutor(client, make_config(tmp_path))
    executor.state.reset_day(NOW, 5000.0)
    executor.state.data["v14_21_initial_balance"] = 5000.0
    executor.state.save()

    client.account.equity = 4750.0
    daily = executor.place(signal(), now=NOW)
    assert daily.code == "V14_21_DAILY_DOLLAR_STOP"

    client.account.equity = 4500.0
    executor.state.data["day"]["start_equity"] = 4500.0
    executor.state.save()
    total = executor.place(
        signal(setup="H4_DONCHIAN_BREAKOUT_2"),
        now=NOW,
    )
    assert total.code == "V14_21_OVERALL_DOLLAR_STOP"

    client.account.equity = 5000.0
    executor.state.data["day"]["start_equity"] = 5000.0
    executor.state.data["day"]["v14_21_consecutive_losses"] = 2
    executor.state.save()
    sequence = executor.place(
        signal(setup="H4_DONCHIAN_BREAKOUT_3"),
        now=NOW,
    )
    assert sequence.code == "V14_21_CONSECUTIVE_LOSS_STOP"


def test_stale_tick_is_rejected(tmp_path) -> None:
    client = FakeClient()
    client.tick.time_msc = int((NOW.timestamp() - 30.0) * 1000)
    executor = V1421DemoAutoExecutor(client, make_config(tmp_path))
    result = executor.place(signal(), now=NOW)
    assert result.code == "STALE_TICK"
    assert client.calls == []


def test_read_only_never_transmits(tmp_path) -> None:
    client = FakeClient()
    executor = V1421DemoAutoExecutor(client, make_config(tmp_path))
    result = executor.place(signal(), now=NOW)
    assert result.code == "READ_ONLY_PROPOSAL"
    assert client.calls == []
    assert (tmp_path / "audit.jsonl").exists()


def test_demo_auto_runs_order_check_before_order_send(tmp_path) -> None:
    client = FakeClient()
    executor = V1421DemoAutoExecutor(
        client,
        make_config(tmp_path, "AUTO"),
    )
    result = executor.place(signal(), now=NOW)
    assert result.code == "ORDER_FILLED"
    assert client.calls == ["check", "send"]
    assert result.risk_percent <= 0.55
