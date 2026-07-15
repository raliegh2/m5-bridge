from __future__ import annotations

import pandas as pd

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile
from mt5_ai_bridge.v14_3_all_symbol_ict import (
    ENGINE_BY_SYMBOL,
    PROFILES,
    SETUP_BY_SYMBOL,
    IctProfile,
    generate_candidates,
    validate_registry,
)
from research.v14_3_five_symbol_ict_10y_backtest import (
    NEW_ICT_BASE_RISK,
    NEW_ICT_GUARDS,
    install_all_symbol_ict_profile,
)


def synthetic_h1() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2020-01-06 00:00:00", tz="UTC")
    for index in range(12):
        time = start + pd.Timedelta(hours=index)
        row = {
            "time": time,
            "end": time + pd.Timedelta(hours=1),
            "open": 1.050,
            "high": 1.080,
            "low": 1.020,
            "close": 1.060,
            "atr14": 0.050,
            "body_atr": 0.20,
            "close_location": 0.67,
            "d1_close": 1.20,
            "d1_ema20": 1.15,
            "d1_ema50": 1.10,
            "h4_close": 1.18,
            "h4_ema20": 1.14,
            "h4_ema50": 1.09,
        }
        if index == 7:
            row.update(
                {
                    "open": 1.015,
                    "high": 1.075,
                    "low": 0.990,
                    "close": 1.065,
                    "body_atr": 1.00,
                    "close_location": 0.88,
                }
            )
        if index == 8:
            row.update({"open": 1.066, "high": 1.180, "low": 1.050, "close": 1.160})
        rows.append(row)
    return pd.DataFrame(rows)


def test_registry_covers_all_non_gbp_ict_symbols() -> None:
    validate_registry()
    assert set(PROFILES) == {"EURUSD", "AUDUSD", "USDJPY"}
    assert set(ENGINE_BY_SYMBOL) == {"EURUSD", "AUDUSD", "USDJPY"}
    assert set(SETUP_BY_SYMBOL) == {"EURUSD", "AUDUSD", "USDJPY"}


def test_completed_session_sweep_generates_candidate_after_range_finishes() -> None:
    profile = IctProfile(
        name="test",
        session_start_hour=0,
        session_end_hour=6,
        entry_start_hour=7,
        entry_end_hour=10,
        displacement_atr=0.20,
        sweep_atr=0.0,
        stop_buffer_atr=0.10,
        target_r=1.5,
        max_holding_hours=3,
    )
    candidates = generate_candidates("EURUSD", synthetic_h1(), profile)
    assert len(candidates) == 1
    candidate = candidates.iloc[0]
    assert candidate["engine"] == "EURUSD_ICT_LIQUIDITY"
    assert candidate["setup"] == "eurusd_ict_liquidity"
    assert candidate["entry_time"] == pd.Timestamp("2020-01-06 08:00:00", tz="UTC")
    assert candidate["side"] == "BUY"


def test_new_symbol_risk_profile_is_installed_without_removing_gbp_profiles() -> None:
    original_gbp_keys = {
        key for key in profit_profile.SETUP_RISK_PERCENT if key[0] in {"GBPUSD", "GBPJPY"}
    }
    install_all_symbol_ict_profile()
    assert set(NEW_ICT_BASE_RISK).issubset(profit_profile.SETUP_RISK_PERCENT)
    assert set(NEW_ICT_GUARDS).issubset(profit_profile.SYMBOL_GUARDS)
    assert original_gbp_keys.issubset(profit_profile.SETUP_RISK_PERCENT)
    assert profit_profile.SYMBOL_GUARDS["EURUSD"].max_open_positions == 1


def test_all_new_ict_engines_use_conservative_base_risk() -> None:
    assert NEW_ICT_BASE_RISK[("EURUSD", "eurusd_ict_liquidity")] == 0.14
    assert NEW_ICT_BASE_RISK[("AUDUSD", "audusd_ict_asia_london")] == 0.12
    assert NEW_ICT_BASE_RISK[("USDJPY", "usdjpy_ict_session_sweep")] == 0.10
    assert max(NEW_ICT_BASE_RISK.values()) <= 0.14
