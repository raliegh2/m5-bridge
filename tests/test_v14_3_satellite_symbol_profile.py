from __future__ import annotations

import pandas as pd

from mt5_ai_bridge.v14_3_satellite_symbol_profile import (
    EURUSD_RANGE_ATR_MAX,
    EURUSD_RANGE_ATR_MIN,
    RISK,
    apply_satellite_v12_risk,
    filter_satellite_ict,
)


def ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def test_v12_satellite_risk_promotes_only_approved_sleeves() -> None:
    frame = pd.DataFrame([
        {"entry_time": ts("2020-01-02 12:00"), "symbol": "EURUSD", "engine": "EURUSD_SWING_CORE", "risk_percent": 0.35},
        {"entry_time": ts("2020-01-02 12:00"), "symbol": "EURUSD", "engine": "EURUSD_SWING_RETEST", "risk_percent": 0.05},
        {"entry_time": ts("2020-01-02 08:00"), "symbol": "AUDUSD", "engine": "AUDUSD_TREND_PULLBACK", "risk_percent": 0.35},
        {"entry_time": ts("2020-01-02 04:00"), "symbol": "AUDUSD", "engine": "AUDUSD_TREND_PULLBACK", "risk_percent": 0.05},
        {"entry_time": ts("2020-01-02 16:00"), "symbol": "USDJPY", "engine": "USDJPY_SAFE_HAVEN_BREAKOUT", "risk_percent": 0.25},
        {"entry_time": ts("2020-01-02 12:00"), "symbol": "GBPUSD", "engine": "GBPUSD_V10_PRECISION", "risk_percent": 0.40},
    ])
    result = apply_satellite_v12_risk(frame)
    assert result["risk_percent"].tolist() == [
        RISK["EURUSD"].v12_full_risk_percent,
        RISK["EURUSD"].v12_micro_risk_percent,
        RISK["AUDUSD"].v12_full_risk_percent,
        RISK["AUDUSD"].v12_micro_risk_percent,
        RISK["USDJPY"].v12_full_risk_percent,
        0.40,
    ]


def test_ict_filters_use_only_pre_entry_features() -> None:
    frame = pd.DataFrame([
        {
            "symbol": "EURUSD", "engine": "EURUSD_ICT_LIQUIDITY", "setup": "eurusd_ict_liquidity",
            "side": "BUY", "entry_time": ts("2020-01-06 14:00"), "exit_time": ts("2020-01-06 16:00"),
            "r_multiple": 2.0, "session_high": 1180.0, "session_low": 1000.0, "signal_atr": 105.0,
        },
        {
            "symbol": "EURUSD", "engine": "EURUSD_ICT_LIQUIDITY", "setup": "eurusd_ict_liquidity",
            "side": "BUY", "entry_time": ts("2020-01-07 14:00"), "exit_time": ts("2020-01-07 16:00"),
            "r_multiple": -1.0, "session_high": 1300.0, "session_low": 1000.0, "signal_atr": 100.0,
        },
        {
            "symbol": "AUDUSD", "engine": "AUDUSD_ICT_ASIA_LONDON", "setup": "audusd_ict_asia_london",
            "side": "SELL", "entry_time": ts("2020-01-06 09:00"), "exit_time": ts("2020-01-06 12:00"),
            "r_multiple": 1.5, "session_high": 1100.0, "session_low": 900.0, "signal_atr": 100.0,
        },
        {
            "symbol": "AUDUSD", "engine": "AUDUSD_ICT_ASIA_LONDON", "setup": "audusd_ict_asia_london",
            "side": "SELL", "entry_time": ts("2020-01-09 09:00"), "exit_time": ts("2020-01-09 12:00"),
            "r_multiple": 1.5, "session_high": 1100.0, "session_low": 900.0, "signal_atr": 100.0,
        },
        {
            "symbol": "USDJPY", "engine": "USDJPY_ICT_SESSION_SWEEP", "setup": "usdjpy_ict_session_sweep",
            "side": "BUY", "entry_time": ts("2020-01-06 15:00"), "exit_time": ts("2020-01-06 18:00"),
            "r_multiple": 1.5, "session_high": 1100.0, "session_low": 900.0, "signal_atr": 100.0,
        },
        {
            "symbol": "USDJPY", "engine": "USDJPY_ICT_SESSION_SWEEP", "setup": "usdjpy_ict_session_sweep",
            "side": "SELL", "entry_time": ts("2020-01-06 17:00"), "exit_time": ts("2020-01-06 18:00"),
            "r_multiple": 1.5, "session_high": 1100.0, "session_low": 900.0, "signal_atr": 100.0,
        },
    ])
    selected = filter_satellite_ict(frame)
    assert len(selected) == 4
    assert set(selected["symbol"]) == {"EURUSD", "AUDUSD", "USDJPY"}
    eurusd = selected[selected["symbol"] == "EURUSD"]
    assert len(eurusd) == 1
    assert EURUSD_RANGE_ATR_MIN < float(eurusd.iloc[0]["range_atr"]) <= EURUSD_RANGE_ATR_MAX
    audusd = selected[selected["symbol"] == "AUDUSD"]
    assert len(audusd) == 1
    assert int(audusd.iloc[0]["weekday"]) != 3


def test_satellite_risk_remains_bounded() -> None:
    assert RISK["EURUSD"].ict_risk_percent <= 0.60
    assert RISK["AUDUSD"].ict_risk_percent <= 0.50
    assert RISK["USDJPY"].ict_risk_percent <= 0.60
