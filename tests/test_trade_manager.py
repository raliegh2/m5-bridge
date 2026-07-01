from mt5_ai_bridge.trade_manager import close_position
from tests.fakes import (FakeMT5Client, make_order_result, make_position,
                         make_tick)


def test_close_buy_position_sends_sell_at_bid():
    pos = make_position(ticket=42, ptype=FakeMT5Client.POSITION_TYPE_BUY,
                        volume=0.05)
    client = FakeMT5Client(positions=[pos], tick=make_tick(bid=1.2343, ask=1.2345),
                           order_result=make_order_result())
    ok, msg = close_position(client, 42)
    assert ok
    req = client.sent_requests[-1]
    assert req["type"] == client.ORDER_TYPE_SELL
    assert req["price"] == 1.2343
    assert req["position"] == 42
    assert req["volume"] == 0.05


def test_close_sell_position_sends_buy_at_ask():
    pos = make_position(ticket=7, ptype=FakeMT5Client.POSITION_TYPE_SELL)
    client = FakeMT5Client(positions=[pos], tick=make_tick(bid=1.2343, ask=1.2345),
                           order_result=make_order_result())
    ok, _ = close_position(client, 7)
    assert ok
    assert client.sent_requests[-1]["type"] == client.ORDER_TYPE_BUY
    assert client.sent_requests[-1]["price"] == 1.2345


def test_close_missing_position_returns_error():
    client = FakeMT5Client(positions=[])
    ok, msg = close_position(client, 999)
    assert not ok
    assert "not found" in msg.lower()


def test_close_rejected_returns_error():
    pos = make_position(ticket=1, ptype=FakeMT5Client.POSITION_TYPE_BUY)
    client = FakeMT5Client(positions=[pos], tick=make_tick(),
                           order_result=make_order_result(retcode=10006,
                                                          comment="Rejected"))
    ok, msg = close_position(client, 1)
    assert not ok
    assert "rejected" in msg.lower()
