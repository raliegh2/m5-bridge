from __future__ import annotations

import sys
from types import SimpleNamespace

from mt5_ai_bridge.mt5_client import RealMT5Client


class FakeMT5(SimpleNamespace):
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 2
    TRADE_ACTION_SLTP = 3
    ORDER_TIME_GTC = 4
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def symbols_get(self, *args, **kwargs):
        return (SimpleNamespace(name="GBPJPY.a"),)

    def symbol_select(self, symbol, enable):
        return symbol == "GBPJPY.a" and enable is True

    def order_calc_profit(self, *args):
        return -25.0

    def order_check(self, request):
        return SimpleNamespace(retcode=0, request=request)

    def order_send(self, request):
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, request=request)


def test_real_client_exposes_multisymbol_and_validation_calls(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    client = RealMT5Client()

    assert client.symbols_get()[0].name == "GBPJPY.a"
    assert client.symbol_select("GBPJPY.a", True) is True
    assert client.order_calc_profit(0, "GBPJPY.a", 0.1, 200.0, 199.5) == -25.0
    assert client.order_check({"symbol": "GBPJPY.a"}).retcode == 0
    assert client.order_send({"symbol": "GBPJPY.a"}).retcode == fake.TRADE_RETCODE_DONE
    assert client.TRADE_RETCODE_PLACED == fake.TRADE_RETCODE_PLACED
    assert client.TRADE_RETCODE_DONE_PARTIAL == fake.TRADE_RETCODE_DONE_PARTIAL
