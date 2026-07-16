from __future__ import annotations

from types import SimpleNamespace

from mt5_ai_bridge.v14_3_mt5_broker_compat import MT5BrokerCompatibilityClient


class RawClient:
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    SYMBOL_TRADE_EXECUTION_MARKET = 2

    def __init__(
        self,
        *,
        filling_flags: int = 3,
        trade_exemode: int = 2,
        tick_time: int = 1_000_000,
    ) -> None:
        self.filling_flags = filling_flags
        self.trade_exemode = trade_exemode
        self.tick_time = tick_time

    def symbol_info(self, _symbol: str):
        return SimpleNamespace(
            filling_mode=self.filling_flags,
            trade_exemode=self.trade_exemode,
            point=0.00001,
            digits=5,
        )

    def symbol_info_tick(self, _symbol: str):
        return SimpleNamespace(time=self.tick_time, bid=1.1, ask=1.1001)

    def copy_rates_from_pos(self, _symbol, _timeframe, _start, _count):
        return [{"time": self.tick_time - 60, "open": 1.0}]


def test_symbol_filling_flags_are_converted_to_order_enum() -> None:
    both = MT5BrokerCompatibilityClient(RawClient(filling_flags=3))
    assert both.symbol_info("GBPUSD").filling_mode == RawClient.ORDER_FILLING_IOC

    fok_only = MT5BrokerCompatibilityClient(RawClient(filling_flags=1))
    assert fok_only.symbol_info("GBPUSD").filling_mode == RawClient.ORDER_FILLING_FOK

    non_market_without_flags = MT5BrokerCompatibilityClient(
        RawClient(filling_flags=0, trade_exemode=1)
    )
    assert (
        non_market_without_flags.symbol_info("GBPUSD").filling_mode
        == RawClient.ORDER_FILLING_RETURN
    )


def test_whole_hour_broker_offset_is_normalized(monkeypatch) -> None:
    system_epoch = 1_000_000
    raw = RawClient(tick_time=system_epoch + 3 * 3600)
    monkeypatch.setattr(
        "mt5_ai_bridge.v14_3_mt5_broker_compat.time.time",
        lambda: float(system_epoch),
    )
    client = MT5BrokerCompatibilityClient(raw)
    rates = client.copy_rates_from_pos("GBPUSD", "M1", 1, 1)
    assert rates[0]["time"] == system_epoch - 60
    diagnostic = client.compatibility_diagnostics()
    # symbol_info has not been requested yet, so force the filling diagnostic.
    client.symbol_info("GBPUSD")
    diagnostic = client.compatibility_diagnostics()["GBPUSD"]
    assert diagnostic["raw_filling_flags"] == 3
    assert diagnostic["selected_order_filling"] == RawClient.ORDER_FILLING_IOC
    assert diagnostic["detected_clock_offset_seconds"] == 3 * 3600


def test_non_hour_or_stale_drift_is_not_rewritten(monkeypatch) -> None:
    system_epoch = 1_000_000
    monkeypatch.setattr(
        "mt5_ai_bridge.v14_3_mt5_broker_compat.time.time",
        lambda: float(system_epoch),
    )
    raw = RawClient(tick_time=system_epoch + 2 * 3600 + 20 * 60)
    client = MT5BrokerCompatibilityClient(raw)
    rates = client.copy_rates_from_pos("GBPUSD", "M1", 1, 1)
    assert rates[0]["time"] == raw.tick_time - 60
