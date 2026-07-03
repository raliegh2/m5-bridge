from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v12_final_adapter import FinalV12Adapter, NamedEngineSignal


class Client:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self):
        self.account = SimpleNamespace(
            login=123,
            server="Research-Server",
            balance=5000.0,
            equity=5000.0,
        )
        self.tick = SimpleNamespace(bid=1.10000, ask=1.10010)
        self.info = SimpleNamespace(
            digits=5,
            point=0.00001,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
        )
        self.sent = []

    def account_info(self):
        return self.account

    def positions_get(self):
        return []

    def symbol_info(self, symbol):
        return self.info

    def symbol_info_tick(self, symbol):
        return self.tick

    def order_calc_profit(self, order_type, symbol, volume, open_price, close_price):
        return 10.0

    def order_send(self, request):
        self.sent.append(request)
        raise AssertionError("proposal adapter must not submit broker orders")


def signal(**overrides):
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
    return NamedEngineSignal(**data)


def test_signal_requires_timezone_aware_time() -> None:
    with pytest.raises(ValueError):
        signal(signal_time=datetime(2026, 7, 3))


def test_adapter_returns_approved_proposal(tmp_path) -> None:
    reviewed = []

    def approve(summary):
        reviewed.append(summary)
        return True

    client = Client()
    adapter = FinalV12Adapter(
        client,
        state_path=str(tmp_path / "state.json"),
        approval_callback=approve,
    )
    result = adapter.submit(signal())
    assert result.ok
    assert result.code == "APPROVED_PROPOSAL"
    assert result.ticket is None
    assert result.proposal is not None
    assert len(reviewed) == 1
    assert reviewed[0].symbol == "AUDUSD"
    assert not client.sent


def test_adapter_returns_declined_result_without_broker_submission(tmp_path) -> None:
    client = Client()
    adapter = FinalV12Adapter(
        client,
        state_path=str(tmp_path / "state.json"),
        approval_callback=lambda summary: False,
    )
    result = adapter.submit(signal())
    assert not result.ok
    assert result.code == "USER_DECLINED"
    assert not client.sent
