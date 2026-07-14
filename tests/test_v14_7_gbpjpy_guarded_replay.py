from datetime import datetime, timezone

import pandas as pd

from research.v14_7_gbpjpy_guarded_combined_replay import (
    V147CombinedReplay,
    V147Config,
)


UTC = timezone.utc


def test_v147_blocks_overlapping_gbpjpy_and_reduces_post_loss_risk():
    v12 = pd.DataFrame()
    ict = pd.DataFrame([
        {
            "trade_id": 1,
            "symbol": "GBPJPY",
            "entry_time": datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            "exit_time": datetime(2026, 7, 14, 11, 0, tzinfo=UTC),
            "r": -1.0,
        },
        {
            "trade_id": 2,
            "symbol": "GBPJPY",
            "entry_time": datetime(2026, 7, 14, 9, 30, tzinfo=UTC),
            "exit_time": datetime(2026, 7, 14, 10, 30, tzinfo=UTC),
            "r": 1.0,
        },
        {
            "trade_id": 3,
            "symbol": "GBPJPY",
            "entry_time": datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            "exit_time": datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
            "r": -1.0,
        },
        {
            "trade_id": 4,
            "symbol": "GBPJPY",
            "entry_time": datetime(2026, 7, 14, 14, 0, tzinfo=UTC),
            "exit_time": datetime(2026, 7, 14, 15, 0, tzinfo=UTC),
            "r": 1.0,
        },
    ])

    replay = V147CombinedReplay(v12, ict, V147Config())
    trades, skipped, _events, _summary = replay.run()

    assert len(trades) == 2
    assert list(trades["risk_percent"]) == [0.20, 0.10]
    reasons = set(skipped["skip_reason"])
    assert "GBPJPY_ONE_POSITION_LIMIT" in reasons
    assert "SYMBOL_BLOCK_REST_DAY" in reasons


def test_v147_blocks_gbpjpy_outside_utc_session_without_affecting_gbpusd():
    # The V14.6 source replay uses naive timestamps from its CSV ledgers.
    v12 = pd.DataFrame()
    ict = pd.DataFrame([
        {
            "trade_id": 1,
            "symbol": "GBPJPY",
            "entry_time": datetime(2026, 7, 14, 3, 0),
            "exit_time": datetime(2026, 7, 14, 4, 0),
            "r": 1.0,
        },
        {
            "trade_id": 2,
            "symbol": "GBPUSD",
            "entry_time": datetime(2026, 7, 14, 3, 0),
            "exit_time": datetime(2026, 7, 14, 4, 0),
            "r": 1.0,
        },
    ])

    replay = V147CombinedReplay(v12, ict, V147Config())
    trades, skipped, _events, _summary = replay.run()

    assert list(trades["symbol"]) == ["GBPUSD"]
    assert list(skipped["skip_reason"]) == ["GBPJPY_SESSION_BLOCK"]
