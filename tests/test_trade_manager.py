from mt5_ai_bridge.trade_manager import (close_position, managed_stop_loss,
                                         trailing_sl)
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


def test_managed_stop_moves_buy_to_break_even_after_one_r():
    decision = managed_stop_loss(
        is_buy=True,
        entry=1.20000,
        current=1.20105,
        current_sl=1.19900,
        pip=0.0001,
        trail_start_pips=20,
        trail_distance_pips=10,
    )
    assert decision.new_sl == 1.20005
    assert "break-even" in decision.reason


def test_managed_stop_moves_sell_to_break_even_after_one_r():
    decision = managed_stop_loss(
        is_buy=False,
        entry=1.20000,
        current=1.19895,
        current_sl=1.20100,
        pip=0.0001,
        trail_start_pips=20,
        trail_distance_pips=10,
    )
    assert decision.new_sl == 1.19995
    assert "break-even" in decision.reason


def test_managed_stop_locks_profit_after_two_thirds_tp_for_sell():
    decision = managed_stop_loss(
        is_buy=False,
        entry=1.32360,
        current=1.32160,
        current_sl=1.32500,
        pip=0.0001,
        trail_start_pips=40,
        trail_distance_pips=15,
        take_profit=1.32060,
    )
    assert decision.new_sl is not None
    assert decision.new_sl < 1.32360
    assert decision.reason in {"2/3 TP profit lock", "break-even protection"}


def test_trailing_sl_wrapper_stays_backward_compatible():
    new_sl = trailing_sl(
        is_buy=False,
        entry=1.30000,
        current=1.29800,
        current_sl=1.30100,
        pip=0.0001,
        start_pips=20,
        distance_pips=10,
    )
    assert new_sl == 1.29900


def test_managed_stop_never_loosens_existing_stop():
    decision = managed_stop_loss(
        is_buy=True,
        entry=1.20000,
        current=1.20150,
        current_sl=1.20100,
        pip=0.0001,
        trail_start_pips=20,
        trail_distance_pips=10,
    )
    assert decision.new_sl is None
