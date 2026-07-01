"""History exporter: writes an OHLC CSV the backtester can read back."""

import pandas as pd

from mt5_ai_bridge.backtest_books import _load_csv_with_time
from mt5_ai_bridge.export_history import export_history
from tests.fakes import FakeMT5Client


def _rates(n=50):
    return [{"time": 1_700_000_000 + i * 300, "open": 1.20, "high": 1.21,
             "low": 1.19, "close": 1.20 + i * 1e-4, "tick_volume": 100,
             "spread": 2, "real_volume": 0} for i in range(n)]


def test_export_writes_csv(tmp_path):
    client = FakeMT5Client(rates=_rates(40))
    out = str(tmp_path / "GBPUSD_M5.csv")
    count = export_history(client, "GBPUSD", "M5", 1000, out)

    assert count == 40
    df = pd.read_csv(out)
    assert list(df.columns) == ["time", "open", "high", "low", "close"]
    # and the backtester's loader accepts it
    loaded = _load_csv_with_time(out)
    assert len(loaded) == 40 and "time" in loaded.columns


def test_export_no_history(tmp_path):
    client = FakeMT5Client(rates=None)
    assert export_history(client, "GBPUSD", "M5", 100, str(tmp_path / "x.csv")) == 0
