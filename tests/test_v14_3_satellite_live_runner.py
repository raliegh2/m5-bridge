from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v14_3_live_execution import (
    LiveRunnerConfig,
    LiveSignal,
    SatelliteLiveExecutor,
    normalize_volume,
    resolve_broker_symbol,
)
from mt5_ai_bridge.v14_3_live_signals import SELECTED_ICT_PROFILE, SYMBOLS, V12_EXIT_MAP
from v14_3_satellite_live_runner import ENGINE_REGISTRY


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
            balance=5000.0, equity=5000.0, login=12345,
            server="UnitTest-Demo", trade_mode=trade_mode,
        )
        self.info = SimpleNamespace(
            visible=True, point=0.00001, digits=5,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            trade_stops_level=10, filling_mode=1,
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

    def symbol_info(self, symbol):
        return self.info if symbol in {"EURUSD", "EURUSD.a"} else None

    def symbol_info_tick(self, _symbol):
        return self.tick

    def symbols_get(self):
        return [SimpleNamespace(name="EURUSD.a")]

    def symbol_select(self, _symbol, _enable=True):
        return True

    def order_calc_profit(self, _order_type, _symbol, volume, price_open, price_close):
        return -abs(price_open - price_close) * 100000.0 * float(volume)

    def order_check(self, _request):
        self.calls.append("check")
        return SimpleNamespace(retcode=0, comment="Done")

    def order_send(self, _request):
        self.calls.append("send")
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=98765, deal=98765, comment="Done")

    def last_error(self):
        return (0, "OK")


def signal() -> LiveSignal:
    # Use a fixed candle timestamp so duplicate-key tests do not depend on OS clock
    # resolution. Live strategy signals are candle-based and therefore stable.
    return LiveSignal(
        symbol="EURUSD",
        broker_symbol="EURUSD",
        engine="EURUSD_ICT_LIQUIDITY",
        setup="eurusd_ict_liquidity",
        mode="ICT",
        side="BUY",
        signal_time=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        requested_risk_percent=0.55,
        stop_pips=20.0,
        target_pips=40.0,
        metadata={"range_atr": 1.7},
    )


def config(tmp_path, mode="READ_ONLY", **kwargs) -> LiveRunnerConfig:
    values = {
        "execution_mode": mode,
        "state_path": str(tmp_path / "state.json"),
        "max_live_risk_percent": 0.25,
        "forward_gate_passed": False,
        "allow_demo_auto": False,
        "max_open_positions": 5,
        "max_open_risk_percent": 1.5,
        "daily_account_loss_limit_percent": 4.0,
        "live_hard_drawdown_percent": 6.0,
        "max_deviation_points": 10,
        "maximum_signal_age_minutes": 90,
    }
    values.update(kwargs)
    result = LiveRunnerConfig(**values)
    result.validate()
    return result


def test_read_only_never_checks_or_sends(tmp_path) -> None:
    client = FakeClient()
    executor = SatelliteLiveExecutor(client, config(tmp_path))
    result = executor.place(signal(), now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc))
    assert result.ok
    assert result.code == "READ_ONLY_PROPOSAL"
    assert client.calls == []
    assert result.risk_percent <= 0.25


def test_read_only_duplicate_is_blocked(tmp_path) -> None:
    client = FakeClient()
    executor = SatelliteLiveExecutor(client, config(tmp_path))
    now = datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc)
    assert executor.place(signal(), now=now).code == "READ_ONLY_PROPOSAL"
    assert executor.place(signal(), now=now).code == "DUPLICATE_SIGNAL"


def test_approval_requires_exact_yes_before_order_check(tmp_path) -> None:
    client = FakeClient()
    now = datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc)
    declined = SatelliteLiveExecutor(client, config(tmp_path, "APPROVAL"), approval_callback=lambda _proposal: False)
    assert declined.place(signal(), now=now).code == "APPROVAL_DECLINED"
    assert client.calls == []

    other_state = tmp_path / "approved.json"
    approved_config = LiveRunnerConfig(**{**config(tmp_path, "APPROVAL").__dict__, "state_path": str(other_state)})
    approved = SatelliteLiveExecutor(client, approved_config, approval_callback=lambda _proposal: True)
    result = approved.place(signal(), now=now)
    assert result.code == "ORDER_FILLED"
    assert client.calls == ["check", "send"]


def test_auto_is_closed_without_both_gates(tmp_path) -> None:
    client = FakeClient()
    executor = SatelliteLiveExecutor(client, config(tmp_path, "AUTO"))
    result = executor.place(
        signal(),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert result.code == "AUTO_GATE_CLOSED"
    assert client.calls == []


def test_auto_can_transmit_only_on_demo_with_both_gates(tmp_path) -> None:
    client = FakeClient()
    now = datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc)
    gated = config(
        tmp_path, "AUTO", forward_gate_passed=True,
        allow_demo_auto=True, max_live_risk_percent=0.25,
    )
    assert SatelliteLiveExecutor(client, gated).place(signal(), now=now).code == "ORDER_FILLED"
    assert client.calls == ["check", "send"]

    real_client = FakeClient(trade_mode=FakeClient.ACCOUNT_TRADE_MODE_REAL)
    real_executor = SatelliteLiveExecutor(real_client, config(tmp_path / "real", "APPROVAL"), approval_callback=lambda _p: True)
    assert real_executor.place(signal(), now=now).code == "DEMO_ACCOUNT_REQUIRED"
    assert real_client.calls == []


def test_drawdown_stop_blocks_before_broker_validation(tmp_path) -> None:
    client = FakeClient()
    executor = SatelliteLiveExecutor(client, config(tmp_path))
    executor.state.data["peak_equity"] = 6000.0
    executor.state.save()
    result = executor.place(
        signal(),
        now=datetime(2026, 7, 15, 12, 1, tzinfo=timezone.utc),
    )
    assert result.code == "LIVE_DRAWDOWN_STOP"
    assert client.calls == []


def test_resolve_suffix_and_volume_round_down() -> None:
    client = FakeClient()
    assert resolve_broker_symbol(client, "EURUSD") == "EURUSD"
    client.symbol_info = lambda symbol: client.info if symbol == "EURUSD.a" else None
    assert resolve_broker_symbol(client, "EURUSD") == "EURUSD.a"
    assert normalize_volume(client.info, 0.067) == 0.06
    assert normalize_volume(client.info, 0.009) == 0.0


def test_configuration_rejects_unvalidated_risk_above_quarter_percent() -> None:
    invalid = LiveRunnerConfig(max_live_risk_percent=0.45, forward_gate_passed=False)
    with pytest.raises(ValueError):
        invalid.validate()


def test_live_registry_excludes_negative_usdjpy_engine() -> None:
    assert SYMBOLS == ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD")
    assert all(symbol != "USDJPY" for symbol, _mode, _engine in ENGINE_REGISTRY)
    assert {engine for engine, _setup in V12_EXIT_MAP} == {
        "GBPUSD_V10_PRECISION", "GBPUSD_SWING_RETEST", "EURUSD_SWING_CORE",
        "EURUSD_SWING_RETEST", "GBPJPY_SWING_CORE", "AUDUSD_TREND_PULLBACK",
    }
    assert SELECTED_ICT_PROFILE == {
        "EURUSD": "eu_ny_20",
        "AUDUSD": "au_london_relaxed",
    }
