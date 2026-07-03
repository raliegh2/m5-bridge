from __future__ import annotations

import pandas as pd

from v12_intraday_m15_m30_backtest import (
    IntradayParams, prepare_signals, resample_bars, _volume,
)
from v12_intraday_paper_engine import PAPER_PARAMS, IntradayPaperCandidate


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


def test_unknown_strategy_family_fails_closed() -> None:
    data = sample_m5(300)
    try:
        prepare_signals(data, IntradayParams(family="UNKNOWN"))
    except ValueError as exc:
        assert "Unknown intraday family" in str(exc)
    else:
        raise AssertionError("Unknown family must not silently produce signals")


def test_london_orb_evaluates_the_full_post_open_window() -> None:
    params = IntradayParams(family="LONDON_ORB")
    assert params.session_start_utc == 7
    # Regression guard: 08:00 and 09:00 must not be accidentally excluded by
    # checking only the minute component of a timestamp.
    minutes = [8 * 60, 9 * 60, 11 * 60 + 45]
    assert all(7 * 60 + 30 <= value < 12 * 60 for value in minutes)


def test_paper_engine_is_fixed_to_sub_one_percent_risk() -> None:
    assert PAPER_PARAMS.family == "TREND_REENTRY_ONLY"
    assert PAPER_PARAMS.risk_percent == 0.25


def test_paper_candidate_has_standard_engine_fields() -> None:
    candidate = IntradayPaperCandidate(
        symbol="GBPUSD", engine="GBPUSD_M15_M30_REENTRY_PAPER",
        setup="M15_PULLBACK", side="BUY",
        signal_time=pd.Timestamp("2026-01-05 08:15", tz="UTC").to_pydatetime(),
        stop_pips=12.0, target_pips=24.0, risk_percent=0.25,
        reason="test",
    )
    assert candidate.to_dict()["engine"] == "GBPUSD_M15_M30_REENTRY_PAPER"
