from __future__ import annotations

import pytest

from mt5_ai_bridge.v14_8_projected_stress_governor import (
    ProjectedStressGovernor,
)
from research.v14_8_strict_all_ten_20k import FROZEN_SLEEVES, SYMBOLS


def test_projected_stress_governor_caps_new_risk() -> None:
    governor = ProjectedStressGovernor(
        maximum_stress_drawdown_percent=9.95,
        minimum_trade_risk_percent=0.025,
    )
    # Peak 10,000 means stressed equity must remain at least 9,005. Balance is
    # 9,500 and existing open risk is 300, leaving 195 risk dollars available.
    maximum = governor.maximum_new_risk_percent(
        balance=9_500.0,
        peak_balance=10_000.0,
        existing_open_risk_dollars=300.0,
    )
    assert maximum == pytest.approx(195.0 / 9_500.0 * 100.0)
    # A proposal larger than the available risk must be clipped to the
    # calculated maximum; a smaller proposal would correctly remain unchanged.
    assert governor.apply(
        3.0,
        balance=9_500.0,
        peak_balance=10_000.0,
        existing_open_risk_dollars=300.0,
    ) == pytest.approx(maximum)


def test_projected_stress_governor_rejects_subminimum_trade() -> None:
    governor = ProjectedStressGovernor(
        maximum_stress_drawdown_percent=9.95,
        minimum_trade_risk_percent=0.025,
    )
    # Only one dollar of stress capacity remains: about 0.0111% of balance,
    # below the 0.025% minimum executable risk tier.
    approved = governor.apply(
        0.50,
        balance=9_006.0,
        peak_balance=10_000.0,
        existing_open_risk_dollars=0.0,
    )
    assert approved == 0.0


def test_every_symbol_has_one_swing_and_one_ict_sleeve() -> None:
    assert len(FROZEN_SLEEVES) == 10
    pairs = {(sleeve.symbol, sleeve.mode) for sleeve in FROZEN_SLEEVES}
    assert pairs == {
        (symbol, mode)
        for symbol in SYMBOLS
        for mode in ("SWING", "ICT")
    }
    assert len({sleeve.setup for sleeve in FROZEN_SLEEVES}) == 10


def test_frozen_risk_limits_and_profile_isolation() -> None:
    for sleeve in FROZEN_SLEEVES:
        if sleeve.mode == "SWING":
            assert 0.0 < sleeve.risk_percent <= 1.25
        else:
            assert 0.0 < sleeve.risk_percent <= 0.60
            assert sleeve.profile is not None

    ict_profiles = {
        (sleeve.symbol, sleeve.profile)
        for sleeve in FROZEN_SLEEVES
        if sleeve.mode == "ICT"
    }
    assert ict_profiles == {
        ("GBPUSD", "gu_london_25"),
        ("EURUSD", "eu_london_20"),
        ("GBPJPY", "gj_ny_20"),
        ("AUDUSD", "au_london_relaxed"),
        ("USDJPY", "ICT_BREAKOUT_H4"),
    }
