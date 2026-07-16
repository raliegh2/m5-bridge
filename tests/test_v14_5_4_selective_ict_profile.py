from __future__ import annotations

import pandas as pd
import pytest

from mt5_ai_bridge.v14_5_4_selective_ict_profile import (
    PROMOTED_WIDE_ICT_RISK,
    V14_5_4_OBSERVATION_RISK_PERCENT,
    apply_selective_ict_profile,
    selective_ict_risk_percent,
    selective_ict_tier,
    selective_setup_name,
)


def test_frozen_promoted_risk_table() -> None:
    assert PROMOTED_WIDE_ICT_RISK == {
        ("EURUSD", "eu_ny_20"): 0.25,
        ("AUDUSD", "au_london_relaxed"): 0.15,
    }


def test_promoted_and_observation_risk() -> None:
    assert selective_ict_risk_percent("EURUSD", "eu_ny_20") == pytest.approx(0.25)
    assert selective_ict_risk_percent("AUDUSD", "au_london_relaxed") == pytest.approx(0.15)
    assert selective_ict_risk_percent("USDJPY", "uj_ny_relaxed") == pytest.approx(
        V14_5_4_OBSERVATION_RISK_PERCENT
    )
    assert selective_ict_risk_percent("GBPUSD", "breakout_60_fade") == pytest.approx(
        V14_5_4_OBSERVATION_RISK_PERCENT
    )


def test_tier_and_setup_naming_are_stable() -> None:
    assert selective_ict_tier("EURUSD", "eu_ny_20") == "PROMOTED_WIDE_ICT"
    assert selective_ict_tier("USDJPY", "uj_ny_relaxed") == "ICT_OBSERVATION"
    assert selective_setup_name("EURUSD", "eu_ny_20") == "v14_5_4_eurusd_eu_ny_20"


def test_candidate_application_keeps_only_promoted_profiles() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "EURUSD",
                "engine": "EURUSD_ICT_LIQUIDITY",
                "setup": "eurusd_ict_liquidity",
                "profile": "eu_ny_20",
                "side": "BUY",
                "entry_time": pd.Timestamp("2026-07-13T12:00:00Z"),
                "exit_time": pd.Timestamp("2026-07-14T12:00:00Z"),
                "r_multiple": 1.5,
                "session_high": 1.1,
                "session_low": 1.0,
                "signal_atr": 0.06,
            },
            {
                "symbol": "USDJPY",
                "engine": "USDJPY_ICT_SESSION_SWEEP",
                "setup": "usdjpy_ict_session_sweep",
                "profile": "uj_ny_relaxed",
                "side": "SELL",
                "entry_time": pd.Timestamp("2026-07-13T15:00:00Z"),
                "exit_time": pd.Timestamp("2026-07-14T15:00:00Z"),
                "r_multiple": 1.5,
                "session_high": 151.0,
                "session_low": 150.0,
                "signal_atr": 0.8,
            },
        ]
    )
    selected = apply_selective_ict_profile(frame)
    assert len(selected) == 1
    row = selected.iloc[0]
    assert row["symbol"] == "EURUSD"
    assert row["risk_percent"] == pytest.approx(0.25)
    assert row["setup"] == "v14_5_4_eurusd_eu_ny_20"
