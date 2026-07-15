from __future__ import annotations

from types import SimpleNamespace

from v14_3_satellite_bot import (
    DASHBOARD_PORT,
    HEARTBEAT_SECONDS,
    SYMBOLS,
    _closed_h1_signature,
    _status_line,
)


def test_clean_bot_defaults_to_one_second_heartbeat_and_old_dashboard_port() -> None:
    assert HEARTBEAT_SECONDS == 1.0
    assert DASHBOARD_PORT == 8800
    assert SYMBOLS == ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")


def test_terminal_status_is_compact_and_contains_account_state() -> None:
    text = _status_line({
        "execution_mode": "AUTO",
        "strategy_state": "WAITING",
        "account": {
            "balance": 5000.0,
            "equity": 5012.5,
            "floating_profit": 12.5,
        },
        "positions": [{"ticket": 1}],
    }, trades_placed=3)
    assert "AUTO" in text
    assert "trades 3" in text
    assert "open 1" in text
    assert "P/L +12.50" in text
    assert "equity $5,012.50" in text
    assert "balance $5,000.00" in text
    assert "engine WAITING" in text
    assert "{" not in text
    assert "\n" not in text


class FakeRatesClient:
    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        del timeframe, start, count
        markers = {
            "GBPUSD": 100,
            "EURUSD": 200,
            "GBPJPY": 300,
            "AUDUSD": 400,
            "USDJPY": 500,
        }
        return [{"time": markers[symbol]}]


def test_closed_h1_signature_covers_all_symbols() -> None:
    broker_map = {symbol: symbol for symbol in SYMBOLS}
    signature = _closed_h1_signature(FakeRatesClient(), broker_map)
    assert signature == (
        ("GBPUSD", 100),
        ("EURUSD", 200),
        ("GBPJPY", 300),
        ("AUDUSD", 400),
        ("USDJPY", 500),
    )
