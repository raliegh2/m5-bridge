from __future__ import annotations

import pandas as pd

from research.v14_24_fx_gold_profit_path import Trade
from research.v14_24_stress_test import maximum_losing_streak, transform


def trade(day: int, result: float) -> Trade:
    entry = pd.Timestamp(f"2020-01-{day:02d}", tz="UTC")
    return Trade(entry, entry + pd.Timedelta(hours=1), "GBPUSD_V10_PRECISION", "GBPUSD", result)


def test_stress_transform_haircuts_winners_and_inflates_losses() -> None:
    stressed = transform(
        [trade(1, 2.0), trade(2, -1.0)],
        extra_cost_r=0.1,
        winner_multiplier=0.8,
        loss_multiplier=1.2,
    )
    assert [item.net_r for item in stressed] == [1.5, -1.3]


def test_maximum_losing_streak_uses_exit_order() -> None:
    assert maximum_losing_streak(
        [trade(1, -1), trade(2, -1), trade(3, 2), trade(4, -1)]
    ) == 2
