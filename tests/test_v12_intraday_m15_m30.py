from __future__ import annotations

import pandas as pd

from v12_intraday_m15_m30_backtest import IntradayParams, resample_bars, _volume


def sample_m5(count: int = 12) -> pd.DataFrame:
    times = pd.date_range("2026-01-05 08:00", periods=count, freq="5min", tz="UTC")
    values = [1.1000 + index * 0.0001 for index in range(count)]
    return pd.DataFrame({
        "time": times, "open": values,
        "high": [value + 0.0002 for value in values],
        "low": [value - 0.0002 for value in values],
        "close": [value + 0.00005 for value in values],
    })


def test_m15_and_m30_use_only_complete_m5_groups() -> None:
    data = sample_m5(11)
    m15 = resample_bars(data, 15)
    m30 = resample_bars(data, 30)
    assert len(m15) == 3
    assert len(m30) == 1
    assert m15.iloc[0].bar_end == pd.Timestamp("2026-01-05 08:15", tz="UTC")
    assert m30.iloc[0].bar_end == pd.Timestamp("2026-01-05 08:30", tz="UTC")


def test_position_volume_is_rounded_down_to_broker_step() -> None:
    assert _volume(5000.0, 0.25, 12.0) == 0.10


def test_default_intraday_risk_stays_below_one_percent() -> None:
    assert IntradayParams().risk_percent == 0.25
    assert IntradayParams().risk_percent < 1.0
