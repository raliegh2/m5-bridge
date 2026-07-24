from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from mt5_ai_bridge.v14_22_order_flow_shadow import (
    evaluate_order_flow_shadow,
)
from mt5_ai_bridge.v14_3_live_execution import LiveSignal


NOW = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)


class TickClient:
    COPY_TICKS_ALL = -1

    def copy_ticks_from(self, symbol, date_from, count, flags):
        del symbol, date_from, count, flags
        mids = [1.1000 + index * 0.00001 for index in range(40)]
        return [
            {
                "time_msc": (
                    int(NOW.timestamp()) - len(mids) + index
                ) * 1000,
                "bid": mid - 0.00005,
                "ask": mid + 0.00005,
            }
            for index, mid in enumerate(mids)
        ]

    def symbol_info(self, symbol):
        del symbol
        return SimpleNamespace(point=0.00001, digits=5)

    def symbol_info_tick(self, symbol):
        del symbol
        return SimpleNamespace(bid=1.1000, ask=1.1001)


def candidate(side: str) -> LiveSignal:
    return LiveSignal(
        symbol="EURUSD",
        broker_symbol="EURUSD",
        engine="EURUSD_SWING_CORE",
        setup="H4_DONCHIAN_BREAKOUT",
        mode="V12",
        side=side,
        signal_time=NOW,
        requested_risk_percent=0.5,
        stop_pips=20.0,
        target_pips=60.0,
    )


def test_bullish_flow_aligns_with_buy_candidate() -> None:
    result = evaluate_order_flow_shadow(TickClient(), candidate("BUY"), now=NOW)
    assert result["verdict"] == "ALIGNED"
    assert result["hypothetical_block"] is False
    assert result["directional_imbalance"] > 0


def test_bullish_flow_conflicts_with_sell_but_remains_hypothetical() -> None:
    result = evaluate_order_flow_shadow(TickClient(), candidate("SELL"), now=NOW)
    assert result["verdict"] == "CONFLICT"
    assert result["hypothetical_block"] is True
    assert result["mode"] == "SHADOW_ONLY"


def test_unavailable_tick_api_fails_open() -> None:
    result = evaluate_order_flow_shadow(object(), candidate("BUY"), now=NOW)
    assert result["verdict"] == "UNAVAILABLE"
    assert result["hypothetical_block"] is False


def test_fresh_centralized_futures_depth_is_preferred() -> None:
    provider = SimpleNamespace(
        reading=lambda _symbol: {
            "state": "READY",
            "imbalance": -0.40,
            "event_count": 100,
            "levels": 10,
        }
    )
    result = evaluate_order_flow_shadow(
        TickClient(),
        candidate("BUY"),
        centralized_provider=provider,
        now=NOW,
    )
    assert result["verdict_source"] == "CENTRALIZED_CME_FUTURES_MBP10"
    assert result["verdict"] == "CONFLICT"
    assert result["directional_imbalance"] == -0.4
    assert result["reading"]["imbalance"] > 0
