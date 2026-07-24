from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from mt5_ai_bridge.v14_21_order_flow import measure_order_flow


class TickClient:
    COPY_TICKS_ALL = -1

    def __init__(self, mids: list[float]) -> None:
        self.mids = mids

    def copy_ticks_from(self, symbol, date_from, count, flags):
        del symbol, date_from, count, flags
        return [
            {
                "time": int(
                    datetime(2026, 7, 23, tzinfo=timezone.utc).timestamp()
                ) - len(self.mids) + index,
                "time_msc": (
                    int(
                        datetime(
                            2026, 7, 23, tzinfo=timezone.utc
                        ).timestamp()
                    )
                    - len(self.mids)
                    + index
                ) * 1000,
                "bid": mid - 0.00005,
                "ask": mid + 0.00005,
                "volume_real": 0,
            }
            for index, mid in enumerate(self.mids)
        ]

    def symbol_info(self, symbol):
        del symbol
        return SimpleNamespace(point=0.00001, digits=5)

    def symbol_info_tick(self, symbol):
        del symbol
        return SimpleNamespace(bid=1.1000, ask=1.1001)


def test_broker_tick_pressure_detects_bullish_flow() -> None:
    reading = measure_order_flow(
        TickClient([1.1000, 1.1001, 1.1002, 1.1001, 1.1003]),
        "EURUSD",
        "EURUSD",
        now=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )
    assert reading["state"] == "BULLISH_PRESSURE"
    assert reading["buy_pressure_percent"] == 75.0
    assert reading["spread_pips"] == 1.0
    assert reading["mode"] == "OBSERVE_ONLY"
    assert set(reading["pressure_windows"]) == {"30s", "2m", "15m"}
    assert reading["pressure_windows"]["30s"]["imbalance"] == 0.5
    assert reading["spread_shock"]["state"] == "NORMAL"
    assert reading["source"] == "BROKER_LOCAL_MT5_PROXY"


def test_order_flow_is_unavailable_without_tick_api() -> None:
    reading = measure_order_flow(
        object(),
        "AUDUSD",
        "AUDUSD",
        now=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )
    assert reading["state"] == "UNAVAILABLE"


def test_absorption_and_spread_shock_proxies_are_recorded() -> None:
    now = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
    base_msc = int(now.timestamp() * 1000)
    rows = []
    for index in range(80):
        mid = 1.1000 + (index % 5) * 0.00001
        spread = 0.00001 if index < 70 else 0.00003
        rows.append({
            "time_msc": base_msc - (79 - index) * 10_000,
            "bid": mid - spread / 2.0,
            "ask": mid + spread / 2.0,
        })

    client = TickClient([])
    client.copy_ticks_from = lambda *_args: rows
    client.symbol_info_tick = lambda _symbol: SimpleNamespace(
        bid=1.1000, ask=1.10003
    )
    reading = measure_order_flow(
        client,
        "EURUSD",
        "EURUSD",
        now=now,
    )

    assert reading["absorption"]["state"] == "SELL_SIDE_ABSORPTION_PROXY"
    assert reading["absorption"]["score"] >= 0.25
    assert reading["spread_shock"]["state"] == "SEVERE_SHOCK"
    assert reading["spread_shock"]["ratio"] >= 2.0
