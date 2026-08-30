from __future__ import annotations

import pandas as pd

from research.v14_24_fx_gold_profit_path import Trade, replay_events


def ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def test_replay_realizes_profit_at_exit_not_at_next_entry() -> None:
    trades = [
        Trade(ts("2020-01-01"), ts("2020-01-03"), "GBPUSD_V10_PRECISION", "GBPUSD", 10.0),
        Trade(ts("2020-01-02"), ts("2020-01-04"), "GBPUSD_V10_PRECISION", "GBPUSD", 1.0),
    ]
    result = replay_events(trades, "2020-01-01", "2021-01-01", apply_governor=False)
    # Both positions risk $35 from the unchanged $5,000 entry-time balance.
    assert result["ending_balance"] == 5385.0
    assert result["wins"] == 2
    assert result["losses"] == 0
    assert result["win_rate_percent"] == 100.0


def test_replay_respects_combined_open_risk_cap() -> None:
    trades = [
        Trade(ts("2020-01-01"), ts("2020-01-10"), "GBPUSD_V10_PRECISION", "GBPUSD", 1.0),
        Trade(ts("2020-01-02"), ts("2020-01-10"), "GBPUSD_V10_PRECISION", "GBPUSD", 1.0),
        Trade(ts("2020-01-03"), ts("2020-01-10"), "GBPUSD_V10_PRECISION", "GBPUSD", 1.0),
        Trade(ts("2020-01-04"), ts("2020-01-10"), "GBPUSD_V10_PRECISION", "GBPUSD", 1.0),
        Trade(ts("2020-01-05"), ts("2020-01-10"), "GOLD_DAILY_TREND", "XAUUSD", 1.0),
    ]
    result = replay_events(trades, "2020-01-01", "2021-01-01", apply_governor=False)
    assert result["trades"] == 4
    assert result["skipped_open_risk"] == 1
