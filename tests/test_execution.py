import pytest

from mt5_ai_bridge.enums import OrderSide
from mt5_ai_bridge.execution import place_market_order, pip_size
from tests.fakes import (FakeMT5Client, make_order_result, make_symbol_info,
                         make_tick)


def _client(**kwargs):
    defaults = dict(
        tick=make_tick(bid=1.2343, ask=1.2345),
        symbol_info=make_symbol_info(digits=5, point=0.00001),
        order_result=make_order_result(retcode=10009, order=555),
    )
    defaults.update(kwargs)
    return FakeMT5Client(**defaults)


def test_pip_size_for_5_digit_symbol():
    client = _client()
    assert pip_size(client, "GBPUSD") == pytest.approx(0.0001)


def test_buy_order_sets_correct_price_sl_tp():
    client = _client()
    ok, msg = place_market_order(client, "GBPUSD", OrderSide.BUY, 0.01,
                                 stop_loss_pips=30, take_profit_pips=60)
    assert ok
    assert "555" in msg
    req = client.sent_requests[-1]
    assert req["type"] == client.ORDER_TYPE_BUY
    assert req["price"] == pytest.approx(1.2345)
    assert req["sl"] == pytest.approx(1.2315)   # ask - 30 pips
    assert req["tp"] == pytest.approx(1.2405)   # ask + 60 pips


def test_sell_order_sets_correct_price_sl_tp():
    client = _client()
    ok, msg = place_market_order(client, "GBPUSD", "SELL", 0.01,
                                 stop_loss_pips=30, take_profit_pips=60)
    assert ok
    req = client.sent_requests[-1]
    assert req["type"] == client.ORDER_TYPE_SELL
    assert req["price"] == pytest.approx(1.2343)   # bid
    assert req["sl"] == pytest.approx(1.2373)      # bid + 30 pips
    assert req["tp"] == pytest.approx(1.2283)      # bid - 60 pips


def test_rejected_order_returns_false():
    client = _client(order_result=make_order_result(retcode=10006, comment="Rejected"))
    ok, msg = place_market_order(client, "GBPUSD", OrderSide.BUY, 0.01)
    assert not ok
    assert "rejected" in msg.lower()


def test_no_tick_returns_error():
    client = _client(tick=None)
    ok, msg = place_market_order(client, "GBPUSD", OrderSide.BUY, 0.01)
    assert not ok
    assert "tick" in msg.lower()


def test_none_result_returns_error():
    client = _client(order_result=None)
    ok, msg = place_market_order(client, "GBPUSD", OrderSide.BUY, 0.01)
    assert not ok
    assert "failed" in msg.lower()
