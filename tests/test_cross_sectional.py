"""Cross-sectional momentum: no lookahead, genuinely neutral, costs charged."""

import numpy as np
import pandas as pd
import pytest

from mt5_ai_bridge.cross_sectional import (CrossSectionalConfig, LOCKED_CS,
                                           build_panel, locked_config,
                                           momentum_scores,
                                           replay_cross_sectional)


def _panel(paths, start=1_000_000_000):
    """paths: {symbol: [close, ...]} -> a time x symbol panel."""
    n = len(next(iter(paths.values())))
    index = [start + i * 86_400 for i in range(n)]
    return pd.DataFrame(paths, index=index)


def _small_cfg(**kwargs):
    base = dict(lookback_days=20, skip_days=2, holding_days=5,
                n_positions=2, min_names=4)
    base.update(kwargs)
    return CrossSectionalConfig(**base)


def test_locked_file_matches_the_code():
    assert locked_config() == LOCKED_CS


def test_config_rejects_an_incoherent_specification():
    with pytest.raises(ValueError):
        CrossSectionalConfig(lookback_days=10, skip_days=20).validate()
    with pytest.raises(ValueError):
        CrossSectionalConfig(holding_days=0).validate()
    with pytest.raises(ValueError):
        CrossSectionalConfig(n_positions=0).validate()
    # Ranking the top and bottom 20 of 30 names is not a cross-section.
    with pytest.raises(ValueError):
        CrossSectionalConfig(n_positions=20, min_names=30).validate()


def test_scores_never_see_the_bar_they_are_scored_on():
    # A symbol that explodes on the final bar must not score highly on it:
    # the score ends skip_days before.
    n = 40
    flat = [100.0] * n
    spike = [100.0] * (n - 1) + [1_000.0]
    panel = _panel({"A": flat, "B": spike})
    cfg = _small_cfg(n_positions=1, min_names=2)

    scores = momentum_scores(panel, cfg)

    assert not np.isfinite(scores["B"].iloc[-1]) or scores["B"].iloc[-1] == 0.0


def test_a_market_wide_move_cancels_and_only_costs_remain():
    # Every name follows an identical path: there is no cross-section, so a
    # neutral book must return exactly its costs, not the market's direction.
    n = 80
    path = [100.0 * (1.02 ** i) for i in range(n)]
    panel = _panel({name: list(path) for name in "ABCDEF"})
    cfg = _small_cfg()

    gross = replay_cross_sectional(panel, cfg, None, 10_000.0)

    assert gross.periods > 0
    assert all(abs(r) < 1e-9 for r in gross.period_returns)


def test_persistent_ranking_pays_and_reversal_does_not():
    # Winners keep winning: the book should make money gross.
    n = 120
    rising = [100.0 * (1.01 ** i) for i in range(n)]
    falling = [100.0 * (0.99 ** i) for i in range(n)]
    flatish = [100.0] * n
    panel = _panel({"W1": rising, "W2": rising, "L1": falling, "L2": falling,
                    "M1": flatish, "M2": flatish})
    cfg = _small_cfg()

    result = replay_cross_sectional(panel, cfg, None, 10_000.0)

    assert result.periods > 0
    assert result.net_profit > 0


def test_costs_are_subtracted_and_scale_with_the_spread():
    n = 120
    rising = [100.0 * (1.01 ** i) for i in range(n)]
    falling = [100.0 * (0.99 ** i) for i in range(n)]
    flat = [100.0] * n
    panel = _panel({"W1": rising, "W2": rising, "L1": falling, "L2": falling,
                    "M1": flat, "M2": flat})
    cfg = _small_cfg()

    gross = replay_cross_sectional(panel, cfg, None, 10_000.0)
    cheap = replay_cross_sectional(panel, cfg,
                                   {s: 0.1 for s in panel.columns}, 10_000.0)
    dear = replay_cross_sectional(panel, cfg,
                                  {s: 1.0 for s in panel.columns}, 10_000.0)

    assert gross.net_profit > cheap.net_profit > dear.net_profit


def test_a_period_is_skipped_when_the_cross_section_is_too_thin():
    n = 80
    panel = _panel({"A": [100.0] * n, "B": [101.0] * n})
    cfg = _small_cfg(min_names=4, n_positions=1)

    result = replay_cross_sectional(panel, cfg, None, 10_000.0)

    assert result.periods == 0


def test_build_panel_aligns_symbols_on_shared_timestamps():
    a = pd.DataFrame({"time": [1, 2, 3], "close": [10.0, 11.0, 12.0]})
    b = pd.DataFrame({"time": [2, 3, 4], "close": [20.0, 21.0, 22.0]})

    panel = build_panel({"A": a, "B": b})

    assert list(panel.index) == [1, 2, 3, 4]
    assert panel.loc[2, "A"] == 11.0 and panel.loc[2, "B"] == 20.0
    assert np.isnan(panel.loc[4, "A"])


def test_equity_curve_and_drawdown_follow_the_period_returns():
    n = 120
    rising = [100.0 * (1.01 ** i) for i in range(n)]
    falling = [100.0 * (0.99 ** i) for i in range(n)]
    flat = [100.0] * n
    panel = _panel({"W1": rising, "W2": rising, "L1": falling, "L2": falling,
                    "M1": flat, "M2": flat})

    result = replay_cross_sectional(panel, _small_cfg(), None, 10_000.0)

    compounded = 10_000.0
    for r in result.period_returns:
        compounded *= (1.0 + r)
    assert round(compounded, 2) == result.final_balance
    assert result.max_drawdown_percent >= 0.0


def test_a_name_the_ranking_keeps_is_not_charged_again():
    # Two names permanently at the extremes: the book turns over once, at the
    # first rebalance, and pays nothing after that.
    n = 200
    rising = [100.0 * (1.01 ** i) for i in range(n)]
    falling = [100.0 * (0.99 ** i) for i in range(n)]
    flat = [100.0] * n
    panel = _panel({"W1": rising, "W2": rising, "L1": falling, "L2": falling,
                    "M1": flat, "M2": flat})
    cfg = _small_cfg()

    result = replay_cross_sectional(panel, cfg,
                                    {s: 1.0 for s in panel.columns}, 10_000.0)

    assert result.cost_drag[0] > 0
    assert sum(result.cost_drag[1:]) == 0.0


def test_a_book_that_churns_every_period_pays_every_period():
    # Ranks that flip each period force a full turnover each time.
    n = 200
    rows = {}
    for k, name in enumerate(["A", "B", "C", "D", "E", "F"]):
        series, price = [], 100.0
        for i in range(n):
            # Alternate which names are stretched, period by period.
            up = ((i // 10) + k) % 2 == 0
            price *= 1.02 if up else 0.98
            series.append(price)
        rows[name] = series
    panel = _panel(rows)
    cfg = _small_cfg()

    spreads = {s: 1.0 for s in panel.columns}
    churning = replay_cross_sectional(panel, cfg, spreads, 10_000.0)

    n = 200
    steady = _panel({"W1": [100.0 * (1.01 ** i) for i in range(n)],
                     "W2": [100.0 * (1.01 ** i) for i in range(n)],
                     "L1": [100.0 * (0.99 ** i) for i in range(n)],
                     "L2": [100.0 * (0.99 ** i) for i in range(n)],
                     "M1": [100.0] * n, "M2": [100.0] * n})
    persistent = replay_cross_sectional(steady, cfg, spreads, 10_000.0)

    # A book whose members keep changing pays many times over; one whose
    # members persist pays once.
    assert sum(churning.cost_drag) > 10 * sum(persistent.cost_drag)
    assert len([c for c in churning.cost_drag if c > 0]) > 5
