"""Moving-average timing: no lookahead, out in downtrends, costs on switches."""

import pandas as pd
import pytest

from mt5_ai_bridge.tactical_allocation import (LOCKED_TACTICAL, TacticalConfig,
                                               locked_tactical_config,
                                               replay_timing)


def _bars(closes, start=1_000_000_000):
    return pd.DataFrame({
        "time": [start + i * 86_400 for i in range(len(closes))],
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
    })


def _cfg(**kwargs):
    base = dict(sma_months=2, trading_days_per_month=5)
    base.update(kwargs)
    return TacticalConfig(**base)


def test_locked_file_matches_the_code():
    assert locked_tactical_config() == LOCKED_TACTICAL


def test_config_rejects_a_shorting_variant():
    # Long-or-flat is the published rule; shorting an index below its average
    # is a different strategy with a different risk profile.
    with pytest.raises(ValueError):
        TacticalConfig(long_only=False).validate()
    with pytest.raises(ValueError):
        TacticalConfig(sma_months=1).validate()


def test_a_rising_asset_is_held_throughout():
    bars = _bars([100.0 * (1.01 ** i) for i in range(120)])

    result = replay_timing(bars, _cfg(), 0.0, "UP")

    assert result.periods > 0
    assert all(result.invested)
    assert result.switches <= 1          # one entry, never exits
    assert result.strategy == result.benchmark


def test_a_falling_asset_is_avoided_and_the_loss_is_not_taken():
    bars = _bars([100.0 * (0.99 ** i) for i in range(120)])

    result = replay_timing(bars, _cfg(), 0.0, "DOWN")

    assert result.periods > 0
    assert not any(result.invested)
    assert all(r == 0.0 for r in result.strategy)
    # Holding it would have lost badly; the rule sat out.
    assert sum(result.benchmark) < -0.1


def test_the_decision_never_uses_the_bar_it_trades():
    # A crash on the final day cannot be dodged: the last decision was made
    # a month earlier, and a rule that avoided it would be peeking.
    closes = [100.0 * (1.01 ** i) for i in range(119)] + [1.0]
    bars = _bars(closes)

    result = replay_timing(bars, _cfg(), 0.0, "CRASH")

    assert result.invested[-1] is True
    assert result.strategy[-1] < -0.5


def test_costs_are_charged_on_switches_only():
    # Rises, falls, rises: two switches out and in, and nothing charged while
    # the position simply persists.
    closes = ([100.0 * (1.02 ** i) for i in range(60)]
              + [100.0 * (1.02 ** 59) * (0.97 ** i) for i in range(60)]
              + [100.0 * (1.02 ** 59) * (0.97 ** 59) * (1.02 ** i)
                 for i in range(60)])
    bars = _bars(closes)

    free = replay_timing(bars, _cfg(), 0.0, "SWING")
    charged = replay_timing(bars, _cfg(), 1.0, "SWING")

    assert charged.switches == free.switches
    assert charged.switches >= 2
    # Total cost equals half the spread on each switch, and no more.
    difference = sum(free.strategy) - sum(charged.strategy)
    assert difference == pytest.approx(charged.switches * 0.005, abs=1e-9)


def test_summary_reports_both_sides_and_the_drawdown_difference():
    closes = ([100.0 * (1.02 ** i) for i in range(60)]
              + [100.0 * (1.02 ** 59) * (0.97 ** i) for i in range(60)])
    result = replay_timing(_bars(closes), _cfg(), 0.0, "X")

    summary = result.summary()

    assert summary["symbol"] == "X"
    assert set(summary) >= {"strategy", "buy_and_hold", "time_in_market",
                            "beats_hold_on_sharpe", "drawdown_reduction_pct"}
    # Sitting out the decline must leave a smaller drawdown than holding it.
    assert summary["strategy"]["max_drawdown_pct"] < \
        summary["buy_and_hold"]["max_drawdown_pct"]
    assert summary["drawdown_reduction_pct"] > 0
    assert isinstance(summary["beats_hold_on_sharpe"], bool)


def test_time_in_market_is_a_fraction():
    closes = ([100.0 * (1.02 ** i) for i in range(60)]
              + [100.0 * (1.02 ** 59) * (0.97 ** i) for i in range(60)])
    result = replay_timing(_bars(closes), _cfg(), 0.0, "X")

    assert 0.0 < result.time_in_market < 1.0


def test_bars_without_a_close_are_refused():
    with pytest.raises(ValueError):
        replay_timing(pd.DataFrame({"time": [1, 2, 3]}), _cfg())
