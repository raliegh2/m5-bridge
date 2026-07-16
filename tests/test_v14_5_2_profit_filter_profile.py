from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_ai_bridge.v14_5_2_profit_filter_profile import (
    V14_5_2_OBSERVATION_RISK_PERCENT,
    v14_5_2_filter_reason,
    v14_5_2_risk_percent,
)


def utc(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def test_normal_promoted_trade_keeps_v14_5_1_risk() -> None:
    assert v14_5_2_risk_percent(
        "GBPUSD_V10_PRECISION", "V12", utc(2026, 7, 15, 12)
    ) == pytest.approx(0.75)


def test_eurusd_monday_is_not_filtered_without_16utc_condition() -> None:
    timestamp = utc(2026, 7, 13, 12)  # Monday, but not 16 UTC
    assert v14_5_2_filter_reason("EURUSD_SWING_CORE", timestamp) is None
    assert v14_5_2_risk_percent(
        "EURUSD_SWING_CORE", "V12", timestamp
    ) == pytest.approx(0.75)


def test_eurusd_16utc_is_observation_only() -> None:
    timestamp = utc(2026, 7, 15, 16)  # Wednesday
    assert v14_5_2_filter_reason("EURUSD_SWING_CORE", timestamp) == (
        "EURUSD_16UTC_OBSERVATION"
    )
    assert v14_5_2_risk_percent(
        "EURUSD_SWING_CORE", "V12", timestamp
    ) == pytest.approx(V14_5_2_OBSERVATION_RISK_PERCENT)


def test_gbpjpy_tuesday_is_observation_only() -> None:
    timestamp = utc(2026, 7, 14, 8)  # Tuesday
    assert v14_5_2_filter_reason("GBPJPY_SWING_CORE", timestamp) == (
        "GBPJPY_TUESDAY_OBSERVATION"
    )
    assert v14_5_2_risk_percent(
        "GBPJPY_SWING_CORE", "V12", timestamp
    ) == pytest.approx(V14_5_2_OBSERVATION_RISK_PERCENT)


def test_ict_and_demoted_engines_keep_observation_risk() -> None:
    timestamp = utc(2026, 7, 15, 12)
    assert v14_5_2_risk_percent(
        "ICT_V14_3", "ICT", timestamp
    ) == pytest.approx(V14_5_2_OBSERVATION_RISK_PERCENT)
    assert v14_5_2_risk_percent(
        "AUDUSD_TREND_PULLBACK", "V12", timestamp
    ) == pytest.approx(V14_5_2_OBSERVATION_RISK_PERCENT)


def test_timezone_conversion_is_utc_before_filtering() -> None:
    # 12:00 at UTC-4 is 16:00 UTC, so the EURUSD 16UTC filter must apply.
    timestamp = datetime.fromisoformat("2026-07-15T12:00:00-04:00")
    assert v14_5_2_filter_reason("EURUSD_SWING_CORE", timestamp) == (
        "EURUSD_16UTC_OBSERVATION"
    )


def test_naive_datetime_is_treated_as_utc() -> None:
    timestamp = datetime(2026, 7, 15, 16)
    assert v14_5_2_filter_reason("EURUSD_SWING_CORE", timestamp) == (
        "EURUSD_16UTC_OBSERVATION"
    )


def test_invalid_time_type_fails_closed() -> None:
    with pytest.raises(TypeError):
        v14_5_2_filter_reason("EURUSD_SWING_CORE", object())
