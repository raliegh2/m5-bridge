from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mt5_ai_bridge.v12_final_adapter import FinalV12Adapter, NamedEngineSignal


class Client:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self, trade_mode=0):
        self.account = SimpleNamespace(login=123, server="Demo", balance=5000.0,
                                       equity=5000.0, trade_mode=trade_mode)
        self.tick = SimpleNamespace(bid=1.10000, ask=1.10010)
        self.info = SimpleNamespace(digits=5, point=0.00001, volume_step=0.01,
                                    volume_min=0.01, volume_max=100.0,
                                    filling_mode=1)
        self.sent = []

    def account_info(self): return self.account
    def positions_get(self, **_kwargs): return []
    def symbol_info(self, _symbol): return self.info
    def symbol_info_tick(self, _symbol): return self.tick
    def order_calc_profit(self, *_args): return 10.0
    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=10009, order=1234, deal=0, comment="done")


def signal(**overrides):
    data = dict(symbol="AUDUSD", engine="AUDUSD_TREND_PULLBACK",
                setup="D1_H4_EMA_PULLBACK_04_08UTC", side="BUY",
                base_risk_percent=0.25, stop_pips=50.0, target_pips=100.0,
                signal_time=datetime(2026, 7, 3, tzinfo=timezone.utc))
    data.update(overrides)
    return NamedEngineSignal(**data)


def test_signal_requires_timezone_aware_time() -> None:
    with pytest.raises(ValueError):
        signal(signal_time=datetime(2026, 7, 3))


def test_adapter_routes_signal_to_automatic_demo_executor(tmp_path) -> None:
    client = Client()
    result = FinalV12Adapter(client, state_path=str(tmp_path / "s.json")).submit(signal())
    assert result.ok and result.code == "ORDER_FILLED" and result.ticket == 1234
    assert len(client.sent) == 1


def test_adapter_blocks_mode_mismatch(tmp_path) -> None:
    client = Client(trade_mode=2)
    result = FinalV12Adapter(client, state_path=str(tmp_path / "s.json")).submit(signal())
    assert not result.ok and result.code == "ACCOUNT_MODE_MISMATCH"
    assert not client.sent


def test_adapter_routes_matching_live_account_without_approval(tmp_path) -> None:
    client = Client(trade_mode=2)
    result = FinalV12Adapter(
        client, state_path=str(tmp_path / "s.json"),
        account_mode_provider=lambda: "LIVE",
    ).submit(signal())
    assert result.ok and result.code == "ORDER_FILLED"
    assert len(client.sent) == 1
