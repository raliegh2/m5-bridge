"""Trailing stop: pure ratchet logic + applying it via the client."""

from mt5_ai_bridge.trade_manager import modify_position_sl, trailing_sl
from tests.fakes import FakeMT5Client, make_order_result, make_position

PIP = 0.0001


def test_buy_trails_once_in_profit():
    # entry 1.2700, price 1.2730 -> +30 pips; start 20, distance 15
    new_sl = trailing_sl(True, 1.2700, 1.2730, 0.0, PIP, 20, 15)
    assert new_sl == 1.2715         # 1.2730 - 15 pips


def test_buy_not_enough_profit():
    assert trailing_sl(True, 1.2700, 1.2710, 0.0, PIP, 20, 15) is None


def test_buy_never_loosens_stop():
    # would-be new sl 1.2715 is below the existing 1.2720 -> keep 1.2720
    assert trailing_sl(True, 1.2700, 1.2730, 1.2720, PIP, 20, 15) is None


def test_buy_tightens_when_better():
    assert trailing_sl(True, 1.2700, 1.2730, 1.2710, PIP, 20, 15) == 1.2715


def test_sell_trails_once_in_profit():
    # entry 1.2700, price 1.2670 -> +30 pips short
    new_sl = trailing_sl(False, 1.2700, 1.2670, 0.0, PIP, 20, 15)
    assert new_sl == 1.2685         # 1.2670 + 15 pips


def test_sell_never_loosens_stop():
    assert trailing_sl(False, 1.2700, 1.2670, 1.2680, PIP, 20, 15) is None


def test_modify_position_sl_sends_sltp_request():
    client = FakeMT5Client(order_result=make_order_result())
    pos = make_position(ticket=42, symbol="GBPUSD", tp=1.2900)
    ok, msg = modify_position_sl(client, pos, 1.2715)
    assert ok
    req = client.sent_requests[-1]
    assert req["action"] == client.TRADE_ACTION_SLTP
    assert req["position"] == 42
    assert req["sl"] == 1.2715
    assert req["tp"] == 1.2900


def test_modify_position_sl_rejected():
    client = FakeMT5Client(order_result=make_order_result(retcode=10006,
                                                          comment="Rejected"))
    ok, msg = modify_position_sl(client, make_position(ticket=1), 1.27)
    assert not ok and "rejected" in msg.lower()
