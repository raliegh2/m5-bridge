from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v12_final_execution import FinalExecutionRequest, FinalResearchExecutor
from mt5_ai_bridge.v12_final_state import StateStore


class Client:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_DONE = 10009

    def __init__(self, positions=None, server="Broker-Research", spread=0.00010):
        self._positions = positions or []
        self._account = SimpleNamespace(
            balance=5000.0,
            equity=5000.0,
            login=12345,
            server=server,
        )
        self._tick = SimpleNamespace(bid=1.10000, ask=1.10000 + spread)
        self._info = SimpleNamespace(
            digits=5,
            point=0.00001,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
        )
        self.sent = []

    def account_info(self):
        return self._account

    def positions_get(self):
        return list(self._positions)

    def symbol_info(self, symbol):
        return self._info

    def symbol_info_tick(self, symbol):
        return self._tick

    def order_calc_profit(self, order_type, symbol, volume, open_price, close_price):
        return 10.0

    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=10009, order=9001, comment="Done")

    def last_error(self):
        return (0, "ok")


def request(**overrides):
    data = dict(
        symbol="AUDUSD",
        engine="AUDUSD_TREND_PULLBACK",
        setup="D1_H4_EMA_PULLBACK_04_08UTC",
        side="BUY",
        base_risk_percent=0.25,
        stop_pips=50.0,
        target_pips=100.0,
        signal_time=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    data.update(overrides)
    return FinalExecutionRequest(**data)


def test_executor_requires_approval_callback(tmp_path) -> None:
    with pytest.raises(ValueError):
        FinalResearchExecutor(Client(), None, StateStore(str(tmp_path / "state.json")))


def test_executor_places_profile_compliant_approved_order(tmp_path) -> None:
    reviewed = []

    def approve(summary):
        reviewed.append(summary)
        return True

    client = Client(server="Any-Account-Type")
    executor = FinalResearchExecutor(client, approve, StateStore(str(tmp_path / "state.json")))
    result = executor.place(request())
    assert result.ok
    assert result.ticket == 9001
    assert result.volume == 0.02
    assert len(reviewed) == 1
    assert reviewed[0].engine == "AUDUSD_TREND_PULLBACK"
    assert len(client.sent) == 1


def test_executor_declines_when_user_does_not_approve(tmp_path) -> None:
    client = Client()
    executor = FinalResearchExecutor(client, lambda summary: False,
                                     StateStore(str(tmp_path / "state.json")))
    result = executor.place(request())
    assert not result.ok
    assert result.code == "USER_DECLINED"
    assert not client.sent


def test_executor_does_not_filter_by_account_server(tmp_path) -> None:
    client = Client(server="Broker-Live")
    executor = FinalResearchExecutor(client, lambda summary: True,
                                     StateStore(str(tmp_path / "state.json")))
    result = executor.place(request())
    assert result.ok


def test_executor_rejects_manual_unregistered_position(tmp_path) -> None:
    client = Client(positions=[SimpleNamespace(ticket=42)])
    executor = FinalResearchExecutor(client, lambda summary: True,
                                     StateStore(str(tmp_path / "state.json")))
    result = executor.place(request())
    assert not result.ok
    assert result.code == "UNREGISTERED_POSITION"


def test_executor_rejects_wide_spread(tmp_path) -> None:
    client = Client(spread=0.00040)
    executor = FinalResearchExecutor(client, lambda summary: True,
                                     StateStore(str(tmp_path / "state.json")))
    result = executor.place(request())
    assert not result.ok
    assert result.code == "SPREAD_TOO_WIDE"


def test_executor_rejects_disabled_engine_before_order_send(tmp_path) -> None:
    client = Client()
    executor = FinalResearchExecutor(client, lambda summary: True,
                                     StateStore(str(tmp_path / "state.json")))
    result = executor.place(request(
        symbol="GBPUSD",
        engine="GBPUSD_SWING_CORE",
        setup="H4_DONCHIAN_BREAKOUT",
        base_risk_percent=0.20,
    ))
    assert not result.ok
    assert result.code == "ENGINE_NOT_ALLOWED"
    assert not client.sent
