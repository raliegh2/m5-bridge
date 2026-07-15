from __future__ import annotations

from v14_3_satellite_bot import (
    DASHBOARD_PORT,
    SCAN_INTERVAL_SECONDS,
    SYMBOLS,
    _status_line,
)


def test_clean_bot_defaults_to_one_second_and_old_dashboard_port() -> None:
    assert SCAN_INTERVAL_SECONDS == 1.0
    assert DASHBOARD_PORT == 8800
    assert SYMBOLS == ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY")


def test_terminal_status_is_single_line_and_tracks_trades_account_state() -> None:
    text = _status_line(
        {
            "execution_mode": "AUTO",
            "scan_latency_ms": 845.2,
            "account": {
                "balance": 5000.0,
                "equity": 5012.5,
                "floating_profit": 12.5,
            },
            "positions": [{"ticket": 1}],
        },
        trades_placed=4,
    )
    assert "AUTO" in text
    assert "trades placed 4" in text
    assert "open 1" in text
    assert "balance $5,000.00" in text
    assert "equity $5,012.50" in text
    assert "P/L +12.50" in text
    assert "scan 845 ms" in text
    assert "\n" not in text
    assert "{" not in text
