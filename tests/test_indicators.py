import pandas as pd

from mt5_ai_bridge import indicators
from tests.fakes import FakeMT5Client


def test_ema_of_constant_series_is_constant():
    s = pd.Series([5.0] * 50)
    assert indicators.ema(s, 9).iloc[-1] == 5.0


def test_rsi_of_rising_series_is_high():
    s = pd.Series(range(1, 100), dtype="float64")
    assert indicators.rsi(s, 14).iloc[-1] > 90


def test_rsi_of_falling_series_is_low():
    s = pd.Series(range(100, 1, -1), dtype="float64")
    assert indicators.rsi(s, 14).iloc[-1] < 10


def test_macd_of_constant_series_is_zero():
    s = pd.Series([3.0] * 60)
    macd_line, signal_line, hist = indicators.macd(s)
    assert abs(macd_line.iloc[-1]) < 1e-9
    assert abs(hist.iloc[-1]) < 1e-9


def _make_rates(n=250):
    return [
        {
            "time": 1_700_000_000 + i * 1800,
            "open": 1.20 + i * 0.0001,
            "high": 1.21 + i * 0.0001,
            "low": 1.19 + i * 0.0001,
            "close": 1.20 + i * 0.0001,
            "tick_volume": 100 + i,
        }
        for i in range(n)
    ]


def test_market_snapshot_returns_indicator_dict():
    client = FakeMT5Client(rates=_make_rates())
    snap = indicators.market_snapshot(client, "GBPUSD", "M30")

    assert snap is not None
    assert snap["symbol"] == "GBPUSD"
    for key in ("close", "ema_9", "ema_20", "ema_50", "ema_200", "rsi_14",
                "macd", "macd_signal", "macd_hist"):
        assert isinstance(snap[key], float)


def test_market_snapshot_handles_no_data():
    client = FakeMT5Client(rates=None)
    assert indicators.market_snapshot(client, "GBPUSD") is None
