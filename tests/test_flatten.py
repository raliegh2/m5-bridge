"""close_all_positions: flatten every open trade."""

from mt5_ai_bridge.trade_manager import close_all_positions, close_position
from tests.fakes import (FakeMT5Client, make_order_result, make_position,
                         make_tick)


def _client(positions):
    return FakeMT5Client(positions=positions, tick=make_tick(),
                         order_result=make_order_result())


def test_close_all_closes_every_position():
    positions = [
        make_position(ticket=1, ptype=FakeMT5Client.POSITION_TYPE_BUY, volume=0.09),
        make_position(ticket=2, ptype=FakeMT5Client.POSITION_TYPE_SELL, volume=0.18),
        make_position(ticket=3, ptype=FakeMT5Client.POSITION_TYPE_BUY, volume=0.01),
    ]
    client = _client(positions)
    results = close_all_positions(client)

    assert len(results) == 3
    assert all(ok for _, ok, _ in results)
    assert len(client.sent_requests) == 3
    # each close targets its own ticket
    assert {r["position"] for r in client.sent_requests} == {1, 2, 3}


def test_close_all_with_no_positions():
    client = _client([])
    assert close_all_positions(client) == []
    assert client.sent_requests == []


def test_close_all_reports_failures():
    client = FakeMT5Client(
        positions=[make_position(ticket=9, ptype=FakeMT5Client.POSITION_TYPE_BUY)],
        tick=make_tick(),
        order_result=make_order_result(retcode=10006, comment="Rejected"))
    results = close_all_positions(client)
    assert results[0][0] == 9 and results[0][1] is False
