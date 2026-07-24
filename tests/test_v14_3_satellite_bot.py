from __future__ import annotations

from types import SimpleNamespace
from io import StringIO
import v14_3_satellite_bot as bot
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
    assert SYMBOLS == ("GBPUSD", "EURUSD", "GBPJPY", "AUDUSD")


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
        "scan_schedule": {
            "GBP_ICT": {"last_scan_at": "2026-07-23T22:17:00+00:00"},
            "FX_PORTFOLIO": {"last_scan_at": "2026-07-23T22:00:00+00:00"},
            "GOLD": {"last_scan_at": "2026-07-23T22:00:00+00:00"},
        },
    }, trades_placed=3)
    assert "AUTO" in text
    assert "trades 3" in text
    assert "open 1" in text
    assert "P/L +12.50" in text
    assert "equity $5,012.50" in text
    assert "balance $5,000.00" in text
    assert "engine WAITING" in text
    assert "GBP-M1 22:17" in text
    assert "FX-H1/H4/D1 22:00" in text
    assert "{" not in text
    assert "\n" not in text


def test_terminal_renderer_reuses_one_physical_line(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.setattr(bot.sys, "stdout", output)
    monkeypatch.setattr(
        bot.shutil,
        "get_terminal_size",
        lambda fallback: SimpleNamespace(columns=60, lines=20),
    )
    bot._last_status_width = 0
    diagnostics = {
        "execution_mode": "AUTO",
        "strategy_state": "WAITING",
        "account": {"balance": 5000, "equity": 5000, "floating_profit": 0},
        "positions": [],
    }
    bot._print_status(diagnostics, trades_placed=0)
    bot._print_status(diagnostics, trades_placed=0)
    rendered = output.getvalue()
    assert "\n" not in rendered
    assert rendered.count("\r") == 2
    assert all(len(part) <= 59 for part in rendered.split("\r"))


class FakeRatesClient:
    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        del timeframe, start, count
        markers = {
            "GBPUSD": 100,
            "EURUSD": 200,
            "GBPJPY": 300,
            "AUDUSD": 400,
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
    )
