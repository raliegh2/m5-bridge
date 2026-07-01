"""Multi-timeframe books: swing (H4/D1), NY day/scalp, trend alignment."""

from datetime import datetime, timezone
from unittest.mock import patch

from mt5_ai_bridge.app import _run_books, make_planner_configs
from mt5_ai_bridge.books import build_books, desired_positions, trend_bias
from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.journal import Journal
from mt5_ai_bridge.strategy import Decision
from tests.fakes import (FakeMT5Client, make_account, make_order_result,
                         make_position, make_settings, make_symbol_info,
                         make_tick)

NY = datetime(2026, 6, 29, 15, tzinfo=timezone.utc)
OFF = datetime(2026, 6, 29, 3, tzinfo=timezone.utc)


def _rates(n=60):
    return [{"time": 1_700_000_000 + i * 1800, "open": 1.20, "high": 1.21,
             "low": 1.19, "close": 1.20 + i * 0.0001, "tick_volume": 100}
            for i in range(n)]


def _client(positions=None):
    return FakeMT5Client(account=make_account(), positions=positions or [],
                         tick=make_tick(), symbol_info=make_symbol_info(),
                         rates=_rates(), order_result=make_order_result())


def _buy(conf):
    def fn(_m):
        return Decision(Signal.BUY, "x", conf)
    return fn


def _run(client, settings, strategy_fn, now, positions=None):
    _run_books(client, Journal(":memory:"), settings, strategy_fn,
               make_planner_configs(settings), positions or [], now_utc=now)


def _run_timeframe_signals(client, signals, confidences=None):
    confidences = confidences or {}

    def snapshot(_client, _symbol, timeframe, _bars):
        return {"tf": timeframe, "atr": 0.001}

    def strategy(market):
        tf = market["tf"]
        return Decision(signals[tf], "test", confidences.get(tf, 0.7))

    settings = make_settings(multi_book=True)
    with patch("mt5_ai_bridge.app.market_snapshot", side_effect=snapshot):
        _run(client, settings, strategy, NY)


def test_build_books_shape():
    books = build_books(make_settings())
    assert [b.name for b in books] == ["swing-H4", "swing-D1", "day-M15", "scalp-M5"]
    assert len({b.magic for b in books}) == 4
    assert books[0].ny_only is False and books[2].ny_only is True
    assert desired_positions(books[0], strong=False) == 1
    assert desired_positions(books[2], strong=False) == 1


def test_trend_bias():
    assert trend_bias(Signal.BUY, Signal.BUY) is Signal.BUY
    assert trend_bias(Signal.SELL, Signal.SELL) is Signal.SELL
    assert trend_bias(Signal.BUY, Signal.SELL) is None
    assert trend_bias(Signal.BUY, Signal.WAIT) is None
    assert trend_bias(Signal.WAIT, Signal.WAIT) is None


def test_strong_aligned_trend_opens_swing_and_intraday_engines():
    client = _client()
    _run(client, make_settings(multi_book=True, max_open_positions=7),
         _buy(0.85), NY)
    assert len(client.sent_requests) == 4
    assert len({request["magic"] for request in client.sent_requests}) == 2


def test_strong_trend_off_session_opens_swing_only():
    client = _client()
    _run(client, make_settings(multi_book=True), _buy(0.85), OFF)
    assert len(client.sent_requests) == 2


def test_low_momentum_opens_one_position_per_engine():
    client = _client()
    _run(client, make_settings(multi_book=True), _buy(0.6), NY)
    assert len(client.sent_requests) == 2


def test_wait_signal_opens_nothing():
    client = _client()
    _run(client, make_settings(multi_book=True),
         lambda _m: Decision(Signal.WAIT, "none", 0.3), NY)
    assert client.sent_requests == []


def test_intraday_can_trade_without_d1_h4_alignment_when_h4_is_not_strong():
    client = _client()
    _run_timeframe_signals(client, {
        "M1": Signal.SELL, "M5": Signal.SELL,
        "M15": Signal.SELL, "M30": Signal.SELL,
        "H4": Signal.BUY, "D1": Signal.SELL,
    }, {"H4": 0.6})
    assert len(client.sent_requests) == 1
    assert client.sent_requests[0]["comment"] == "day-M15"


def test_intraday_is_blocked_by_strong_opposing_h4():
    client = _client()
    _run_timeframe_signals(client, {
        "M1": Signal.SELL, "M5": Signal.SELL,
        "M15": Signal.SELL, "M30": Signal.SELL,
        "H4": Signal.BUY, "D1": Signal.WAIT,
    }, {"H4": 0.9})
    assert client.sent_requests == []


def test_existing_book_positions_are_respected():
    settings = make_settings(multi_book=True)
    day = build_books(settings)[2]
    positions = [make_position(ticket=i, ptype=FakeMT5Client.POSITION_TYPE_BUY,
                               magic=day.magic) for i in range(1)]
    client = _client(positions=positions)
    _run(client, settings, _buy(0.85), NY, positions=positions)
    assert len(client.sent_requests) == 3
