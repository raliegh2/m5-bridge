from mt5_ai_bridge.trade_manager import (breakeven_sl, clamp_partial_lots,
                                         close_position, move_to_breakeven,
                                         partial_close, profit_pips)
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


# --- profit_pips ---------------------------------------------------------

def test_profit_pips_sign_for_buy_and_sell():
    assert round(profit_pips(True, 1.2700, 1.2720, 0.0001), 6) == 20.0   # buy profit
    assert round(profit_pips(False, 1.2700, 1.2720, 0.0001), 6) == -20.0  # sell under
    assert profit_pips(True, 1.0, 1.0, 0.0) == 0.0              # guard pip<=0


# --- breakeven_sl (rule floor) ------------------------------------------

def test_breakeven_floor_triggers_only_after_enough_profit():
    # 20 pips in profit, trigger 15 -> stop moves to entry (buy).
    assert breakeven_sl(True, 1.2700, 1.2720, 0.0, 0.0001, 15) == 1.2700
    # only 5 pips in profit -> not yet.
    assert breakeven_sl(True, 1.2700, 1.2705, 0.0, 0.0001, 15) is None


def test_breakeven_floor_never_loosens_an_existing_stop():
    # Already trailing above entry -> BE (entry) would be looser, so no-op.
    assert breakeven_sl(True, 1.2700, 1.2740, 1.2730, 0.0001, 15) is None
    # Offset covers spread on a sell (stop just below entry).
    assert breakeven_sl(False, 1.3000, 1.2960, 0.0, 0.0001, 15, offset_pips=2) == 1.2998


# --- clamp_partial_lots (guardrail) -------------------------------------

def test_partial_fraction_is_bounded_and_step_snapped():
    assert clamp_partial_lots(1.00, 0.3) == 0.30
    assert clamp_partial_lots(1.00, 5.0) == 1.00          # >100% clamped to all
    assert clamp_partial_lots(0.01, 0.5) == 0.0           # too small to split
    # Closing most of a small position would strand < min_lot -> close all.
    assert clamp_partial_lots(0.03, 0.9) == 0.03


# --- partial_close (execution primitive) --------------------------------

def test_partial_close_sends_bounded_opposite_order():
    pos = make_position(ticket=9, ptype=FakeMT5Client.POSITION_TYPE_BUY, volume=0.10)
    client = FakeMT5Client(positions=[pos], tick=make_tick(bid=1.2343, ask=1.2345),
                           order_result=make_order_result())
    ok, msg, lots = partial_close(client, pos, 0.5)
    assert ok and lots == 0.05
    req = client.sent_requests[-1]
    assert req["type"] == client.ORDER_TYPE_SELL and req["price"] == 1.2343
    assert req["volume"] == 0.05 and req["position"] == 9


def test_partial_close_of_tiny_position_is_skipped():
    pos = make_position(ticket=3, volume=0.01)
    client = FakeMT5Client(positions=[pos], tick=make_tick(),
                           order_result=make_order_result())
    ok, msg, lots = partial_close(client, pos, 0.5)
    assert not ok and lots == 0.0
    assert client.sent_requests == []          # never sent an invalid order


# --- move_to_breakeven (execution primitive) ----------------------------

def test_move_to_breakeven_sends_sltp_when_earned():
    pos = make_position(ticket=5, ptype=FakeMT5Client.POSITION_TYPE_BUY,
                        price_open=1.2700, price_current=1.2725, sl=0.0)
    client = FakeMT5Client(positions=[pos], tick=make_tick(),
                           order_result=make_order_result())
    ok, msg = move_to_breakeven(client, pos, 0.0001, trigger_pips=15)
    assert ok
    assert client.sent_requests[-1]["action"] == client.TRADE_ACTION_SLTP
    assert client.sent_requests[-1]["sl"] == 1.2700


def test_move_to_breakeven_noop_when_not_earned():
    pos = make_position(ticket=6, ptype=FakeMT5Client.POSITION_TYPE_BUY,
                        price_open=1.2700, price_current=1.2705, sl=0.0)
    client = FakeMT5Client(positions=[pos], tick=make_tick(),
                           order_result=make_order_result())
    ok, msg = move_to_breakeven(client, pos, 0.0001, trigger_pips=15)
    assert not ok and client.sent_requests == []
