from __future__ import annotations

from types import SimpleNamespace

from mt5_ai_bridge.v14_25_futures_order_flow import (
    DatabentoFuturesOrderFlow,
    mbp10_book_imbalance,
)


def depth_record(instrument_id: int, bid: int, ask: int):
    return SimpleNamespace(
        instrument_id=instrument_id,
        ts_event=123,
        levels=[
            SimpleNamespace(bid_sz=bid, ask_sz=ask)
            for _index in range(10)
        ],
    )


def test_mbp10_uses_top_ten_exchange_depth_levels() -> None:
    imbalance, levels = mbp10_book_imbalance(depth_record(1, 80, 20))
    assert levels == 10
    assert imbalance == 0.6


def test_direct_and_cross_futures_proxies_are_spot_directional() -> None:
    feed = DatabentoFuturesOrderFlow(enabled=True, api_key="test")
    feed._on_record(SimpleNamespace(
        stype_in_symbol="6B.v.0", instrument_id=1
    ))
    feed._on_record(SimpleNamespace(
        stype_in_symbol="6J.v.0", instrument_id=2
    ))
    for _index in range(30):
        feed._on_record(depth_record(1, 80, 20))
        feed._on_record(depth_record(2, 20, 80))

    gbpusd = feed.reading("GBPUSD")
    gbpjpy = feed.reading("GBPJPY")
    assert gbpusd["state"] == "READY"
    assert gbpusd["imbalance"] == 0.6
    assert gbpjpy["state"] == "READY"
    assert gbpjpy["imbalance"] == 0.6
    assert gbpjpy["event_count"] == 30


def test_disabled_feed_fails_open_with_visible_state() -> None:
    feed = DatabentoFuturesOrderFlow(enabled=False, api_key="")
    feed.start()
    result = feed.reading("EURUSD")
    assert result["status"] == "DISABLED"
    assert result["state"] == "WAITING_FOR_DEPTH"
    assert result["imbalance"] is None
