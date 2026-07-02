import pandas as pd

from research.v9_10y_preflight import REQUIREMENTS, run_preflight


def _write_ohlc(path, start, periods, frequency):
    times = pd.date_range(start, periods=periods, freq=frequency, tz="UTC")
    pd.DataFrame({
        "time": times,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.0,
    }).to_csv(path, index=False)


def test_preflight_refuses_missing_history(tmp_path):
    files = {
        requirement.key: tmp_path / f"{requirement.key}.csv"
        for requirement in REQUIREMENTS
    }
    payload = run_preflight(
        files,
        pd.Timestamp("2016-07-01", tz="UTC"),
        pd.Timestamp("2026-07-01", tz="UTC"),
    )
    assert payload["status"] == "BLOCKED_INSUFFICIENT_DATA"
    assert not payload["ten_year_label_allowed"]


def test_preflight_accepts_small_complete_fixture(tmp_path):
    start = pd.Timestamp("2026-01-05", tz="UTC")
    end = pd.Timestamp("2026-01-05 04:00", tz="UTC")
    files = {}
    for requirement in REQUIREMENTS:
        path = tmp_path / f"{requirement.key}.csv"
        frequency = "15min" if requirement.timeframe == "M15" else (
            "4h" if requirement.timeframe == "H4" else "1D"
        )
        periods = 17 if frequency == "15min" else (2 if frequency == "4h" else 1)
        _write_ohlc(path, start, periods, frequency)
        files[requirement.key] = path
    payload = run_preflight(files, start, end)
    assert payload["status"] == "READY"
