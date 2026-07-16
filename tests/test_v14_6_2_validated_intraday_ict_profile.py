from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mt5_ai_bridge.v14_6_2_validated_intraday_ict_profile import (
    PROMOTED_INTRADAY_ICT,
    V14_6_2_OBSERVATION_RISK_PERCENT,
    V14_6_2_PROMOTED_RISK_PERCENT,
    intraday_ict_risk_percent,
    intraday_ict_tier,
    is_promoted_intraday_ict,
)


def utc(hour: int) -> datetime:
    return datetime(2026, 7, 16, hour, tzinfo=timezone.utc)


def test_only_validated_symbols_are_promoted() -> None:
    assert set(PROMOTED_INTRADAY_ICT) == {"GBPJPY", "AUDUSD"}


def test_gbpjpy_validated_component_is_promoted() -> None:
    assert is_promoted_intraday_ict(
        "GBPJPY", "GBPJPY_ICT_INTRADAY_GJ_LONDON_PULLBACK", "SELL", utc(14)
    )
    assert intraday_ict_risk_percent(
        "GBPJPY", "GBPJPY_ICT_INTRADAY_GJ_LONDON_PULLBACK", "SELL", utc(14)
    ) == pytest.approx(V14_6_2_PROMOTED_RISK_PERCENT)


def test_audusd_validated_component_is_promoted() -> None:
    assert intraday_ict_tier(
        "AUDUSD", "AUDUSD_ICT_INTRADAY_AU_ASIA_LONDON_PULLBACK", "BUY", utc(7)
    ) == "PROMOTED_INTRADAY_ICT"


def test_wrong_direction_or_hour_stays_observation() -> None:
    assert intraday_ict_risk_percent(
        "GBPJPY", "GBPJPY_ICT_INTRADAY_GJ_LONDON_PULLBACK", "BUY", utc(14)
    ) == pytest.approx(V14_6_2_OBSERVATION_RISK_PERCENT)
    assert intraday_ict_risk_percent(
        "AUDUSD", "AUDUSD_ICT_INTRADAY_AU_ASIA_LONDON_PULLBACK", "BUY", utc(8)
    ) == pytest.approx(V14_6_2_OBSERVATION_RISK_PERCENT)


def test_gbpusd_remains_shadow_observation() -> None:
    assert intraday_ict_tier(
        "GBPUSD", "GBPUSD_ICT_INTRADAY_GU_LONDON_PULLBACK_PARTIAL", "SELL", utc(8)
    ) == "SHADOW_OBSERVATION"
    assert intraday_ict_risk_percent(
        "GBPUSD", "GBPUSD_ICT_INTRADAY_GU_LONDON_PULLBACK_PARTIAL", "SELL", utc(8)
    ) == pytest.approx(V14_6_2_OBSERVATION_RISK_PERCENT)


def test_timezone_is_normalized_before_hour_gate() -> None:
    local = datetime.fromisoformat("2026-07-16T10:00:00-04:00")
    assert is_promoted_intraday_ict(
        "GBPJPY", "GBPJPY_ICT_INTRADAY_GJ_LONDON_PULLBACK", "SELL", local
    )
