"""High-conviction satellite profiles for the three under-contributing symbols.

The rules use only information available before entry: engine identity, completed
candle time, direction, and session-range/ATR measurements. They are frozen for
research and shadow validation; no broker execution path is included here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

import mt5_ai_bridge.v14_3_profit_preserving_profile as profit_profile
from mt5_ai_bridge.v14_3_profit_preserving_profile import SymbolGuard


TARGET_SYMBOLS = ("EURUSD", "AUDUSD", "USDJPY")


@dataclass(frozen=True)
class SatelliteRisk:
    v12_full_risk_percent: float
    v12_micro_risk_percent: float
    ict_risk_percent: float


RISK: dict[str, SatelliteRisk] = {
    "EURUSD": SatelliteRisk(0.55, 0.025, 0.55),
    "AUDUSD": SatelliteRisk(0.55, 0.025, 0.45),
    "USDJPY": SatelliteRisk(0.15, 0.025, 0.55),
}

# Frozen thresholds selected from the early development segment and required to
# remain positive in confirmation and later-validation partitions.
EURUSD_RANGE_ATR_MIN = 1.60
EURUSD_RANGE_ATR_MAX = 1.80
AUDUSD_EXCLUDED_WEEKDAYS = (3,)  # Thursday
USDJPY_QUALITY_HOURS_UTC = (15,)


SATELLITE_GUARDS: dict[str, SymbolGuard] = {
    "EURUSD": SymbolGuard(
        post_loss_multiplier=0.65,
        max_open_positions=1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=1.10,
        stop_after_daily_losses=3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=6.0,
        win_pressure_recovery=1.0,
        session_start_hour_utc=7,
        session_end_hour_utc=18,
    ),
    "AUDUSD": SymbolGuard(
        post_loss_multiplier=0.70,
        max_open_positions=1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=1.00,
        stop_after_daily_losses=3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=6.0,
        win_pressure_recovery=1.0,
        session_start_hour_utc=6,
        session_end_hour_utc=18,
    ),
    "USDJPY": SymbolGuard(
        post_loss_multiplier=0.65,
        max_open_positions=1,
        max_entries_per_hour=1,
        daily_loss_cap_percent=1.00,
        stop_after_daily_losses=3,
        block_after_consecutive_losses=3,
        rolling_loss_count=3,
        rolling_loss_hours=6.0,
        win_pressure_recovery=1.0,
        session_start_hour_utc=7,
        session_end_hour_utc=19,
    ),
}


def _utc_timestamp(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def apply_satellite_v12_risk(frame: pd.DataFrame) -> pd.DataFrame:
    """Increase only proven V12 sleeves and retain weak streams at micro risk."""
    output = frame.copy()
    output["pre_satellite_risk_percent"] = output["risk_percent"].astype(float)
    tiers: list[str] = []
    risks: list[float] = []

    for row in output.to_dict("records"):
        symbol = str(row["symbol"]).upper()
        engine = str(row["engine"]).upper()
        hour = _utc_timestamp(row["entry_time"]).hour
        original = float(row["risk_percent"])

        if symbol == "EURUSD":
            if engine == "EURUSD_SWING_CORE":
                risks.append(RISK[symbol].v12_full_risk_percent)
                tiers.append("EURUSD_CORE_SATELLITE")
            else:
                risks.append(min(original, RISK[symbol].v12_micro_risk_percent))
                tiers.append("EURUSD_RETEST_MICRO")
        elif symbol == "AUDUSD":
            if hour == 8:
                risks.append(RISK[symbol].v12_full_risk_percent)
                tiers.append("AUDUSD_08UTC_SATELLITE")
            else:
                risks.append(min(original, RISK[symbol].v12_micro_risk_percent))
                tiers.append("AUDUSD_OTHER_MICRO")
        elif symbol == "USDJPY":
            # The exact-ten-year V12 sleeve was weak. It remains enabled for
            # diagnostics and recovery evidence but does not dominate the new ICT sleeve.
            risks.append(min(original, RISK[symbol].v12_full_risk_percent))
            tiers.append("USDJPY_V12_OBSERVATION")
        else:
            risks.append(original)
            tiers.append("UNCHANGED")

    output["risk_percent"] = risks
    output["satellite_v12_tier"] = tiers
    return output


def filter_satellite_ict(candidates: pd.DataFrame) -> pd.DataFrame:
    """Admit robust candidate subsets and attach the frozen satellite tier."""
    if candidates.empty:
        return candidates.copy()
    frame = candidates.copy()
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["weekday"] = frame["entry_time"].dt.weekday
    frame["entry_hour"] = frame["entry_time"].dt.hour
    frame["range_atr"] = (
        frame["session_high"].astype(float) - frame["session_low"].astype(float)
    ) / frame["signal_atr"].astype(float).replace(0.0, pd.NA)

    eurusd = frame[
        (frame["symbol"] == "EURUSD")
        & (frame["range_atr"] > EURUSD_RANGE_ATR_MIN)
        & (frame["range_atr"] <= EURUSD_RANGE_ATR_MAX)
    ].copy()
    eurusd["satellite_ict_tier"] = "EURUSD_RANGE_ATR_QUALITY"

    audusd = frame[
        (frame["symbol"] == "AUDUSD")
        & (frame["side"] == "SELL")
        & (~frame["weekday"].isin(AUDUSD_EXCLUDED_WEEKDAYS))
    ].copy()
    audusd["satellite_ict_tier"] = "AUDUSD_SELL_EX_THURSDAY"

    usdjpy = frame[
        (frame["symbol"] == "USDJPY")
        & ((frame["entry_hour"].isin(USDJPY_QUALITY_HOURS_UTC)) | (frame["side"] == "SELL"))
    ].copy()
    usdjpy["satellite_ict_tier"] = "USDJPY_15UTC_OR_SELL"

    selected = pd.concat([eurusd, audusd, usdjpy], ignore_index=True, sort=False)
    selected = selected.sort_values(["entry_time", "symbol", "engine"])
    selected = selected.drop_duplicates(["entry_time", "exit_time", "symbol", "engine", "side"])
    return selected.reset_index(drop=True)


def install_satellite_ict_risk() -> None:
    """Extend the mutable research profile for the current replay process."""
    for symbol in TARGET_SYMBOLS:
        setup = {
            "EURUSD": "eurusd_ict_liquidity",
            "AUDUSD": "audusd_ict_asia_london",
            "USDJPY": "usdjpy_ict_session_sweep",
        }[symbol]
        profit_profile.SETUP_RISK_PERCENT[(symbol, setup)] = RISK[symbol].ict_risk_percent
    profit_profile.SYMBOL_GUARDS.update(SATELLITE_GUARDS)


def validate_profile() -> None:
    assert EURUSD_RANGE_ATR_MIN < EURUSD_RANGE_ATR_MAX
    assert RISK["EURUSD"].ict_risk_percent <= 0.60
    assert RISK["AUDUSD"].ict_risk_percent <= 0.50
    assert RISK["USDJPY"].ict_risk_percent <= 0.60
    assert all(guard.max_open_positions == 1 for guard in SATELLITE_GUARDS.values())
    assert all(guard.daily_loss_cap_percent <= 1.10 for guard in SATELLITE_GUARDS.values())


validate_profile()
