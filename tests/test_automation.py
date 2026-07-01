"""AUTO execution: minimum-stack burst on strong trends, caps, and guards."""

from mt5_ai_bridge.app import run
from mt5_ai_bridge.enums import Mode, Signal
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.strategy import Decision
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_position, make_settings, make_symbol_info,
                         make_tick)


def _rates(n=60):
    return [{"time": 1_700_000_000 + i * 1800, "open": 1.20, "high": 1.21,
             "low": 1.19, "close": 1.20 + i * 0.0001, "tick_volume": 100}
            for i in range(n)]


def _client(positions=None):
    return FakeMT5Client(
        account=make_account(balance=10000, equity=10000),
        positions=positions or [], tick=make_tick(bid=1.2343, ask=1.2345),
        symbol_info=make_symbol_info(), rates=_rates(),
        order_result=make_order_result(order=777),
    )


def _buy(conf):
    def fn(_m):
        return Decision(Signal.BUY, "forced buy", conf)
    return fn


_STRONG = _buy(0.85)   # >= strong_trend_confidence (0.8)
_WEAK = _buy(0.6)      # < strong


def _buys(n):
    return [make_position(ticket=i, ptype=FakeMT5Client.POSITION_TYPE_BUY)
            for i in range(n)]


def test_strong_trend_opens_minimum_three(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client()
    run(settings=make_settings(mode=Mode.AUTO, db_path=db,
                               min_same_direction=3, max_same_direction=3),
        client=client, journal=Journal(db), strategy_fn=_STRONG, max_iterations=1)

    assert len(client.sent_requests) == 3                 # burst to the minimum
    assert all(r["type"] == client.ORDER_TYPE_BUY for r in client.sent_requests)
    # staggered exits -> the three stops are at distinct prices
    assert len({r["sl"] for r in client.sent_requests}) == 3


def test_weak_trend_opens_single(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client()
    run(settings=make_settings(mode=Mode.AUTO, db_path=db),
        client=client, journal=Journal(db), strategy_fn=_WEAK, max_iterations=1)
    assert len(client.sent_requests) == 1


def test_strong_trend_tops_up_to_minimum(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client(positions=_buys(2))   # already 2 longs
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, min_same_direction=3,
                               max_same_direction=3),
        client=client, journal=Journal(db), strategy_fn=_STRONG, max_iterations=1)
    assert len(client.sent_requests) == 1   # opens just the 3rd


def test_does_not_exceed_max_same_direction(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client(positions=_buys(3))
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, max_same_direction=3),
        client=client, journal=Journal(db), strategy_fn=_STRONG, max_iterations=1)
    assert client.sent_requests == []


def test_burst_respects_max_open_positions(tmp_path):
    db = str(tmp_path / "j.db")
    client = _client()
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, min_same_direction=3,
                               max_same_direction=3, max_open_positions=2),
        client=client, journal=Journal(db), strategy_fn=_STRONG, max_iterations=1)
    assert len(client.sent_requests) == 2   # capped by max open positions


def test_daily_cap_blocks_burst(tmp_path):
    db = str(tmp_path / "j.db")
    journal = Journal(db)
    for _ in range(10):
        journal.log_order("GBPUSD", "BUY", 0.09, None, None, None, 1, "FILLED", "x")
    client = _client()
    run(settings=make_settings(mode=Mode.AUTO, db_path=db, max_trades_per_day=10),
        client=client, journal=journal, strategy_fn=_STRONG, max_iterations=1)
    assert client.sent_requests == []


def test_opposite_direction_allowed(tmp_path):
    db = str(tmp_path / "j.db")
    open_sell = make_position(ticket=1, ptype=FakeMT5Client.POSITION_TYPE_SELL)
    client = _client(positions=[open_sell])
    run(settings=make_settings(mode=Mode.AUTO, db_path=db),
        client=client, journal=Journal(db), strategy_fn=_WEAK, max_iterations=1)
    assert len(client.sent_requests) == 1
    assert client.sent_requests[0]["type"] == client.ORDER_TYPE_BUY
