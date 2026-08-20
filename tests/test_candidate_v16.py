"""The locked V16 mean-reversion candidate."""

import json
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
import pytest
from pytest import approx

from mt5_ai_bridge.candidate_v16 import (LOCK_PATH, LOCKED_V16,
                                         ReversionConfig, add_bands,
                                         locked_config_v16, replay_v16)
from mt5_ai_bridge.costs import RETAIL_TYPICAL, ZERO_COST
from mt5_ai_bridge.enums import Signal
from mt5_ai_bridge.instruments import instrument_for

H4 = 14_400
START = 1_100_000_000


def _bars(closes, pad=0.0010):
    return pd.DataFrame({
        "time": [START + i * H4 for i in range(len(closes))],
        "open": closes,
        "high": [c + pad for c in closes],
        "low": [c - pad for c in closes],
        "close": closes,
    })


def _oscillating(n=800, base=1.2000, phi=0.90, sigma=0.004, seed=3):
    """An Ornstein-Uhlenbeck series: stationary, reverting to a fixed level.

    A sine wave is NOT the right premise-holds test here -- a 20-bar rolling
    mean tracks a smooth cycle, so the z-score never mean-reverts and every
    trade rides the wave into its stop. A stationary process reverting to a
    constant is what the rules actually assume.
    """
    rng = np.random.default_rng(seed)
    x = base
    out = []
    for _ in range(n):
        x = base + phi * (x - base) + rng.normal(0.0, sigma)
        out.append(x)
    return _bars(out)


# --- the lock ---------------------------------------------------------------


def test_lock_file_matches_the_code():
    assert locked_config_v16() == LOCKED_V16


def test_lock_file_predeclares_its_expectation():
    """A pre-registered test states the expected outcome before running."""
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    assert payload["parameters"] == asdict(LOCKED_V16)
    for key in ("hypothesis", "why_this_follows_from_evidence",
                "why_these_parameters", "provenance", "acceptance_gates",
                "data_requirements", "predeclared_expectation"):
        assert payload[key], f"lock file is missing {key}"
    assert "FAIL" in payload["predeclared_expectation"]


def test_tampered_lock_file_is_rejected(tmp_path):
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    payload["parameters"]["entry_z"] = 1.5
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="disagrees with the code"):
        locked_config_v16(path)


def test_missing_lock_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        locked_config_v16(tmp_path / "nope.json")


# --- config validation ------------------------------------------------------


def test_exit_must_be_inside_entry():
    with pytest.raises(ValueError, match="exit_z"):
        replace(LOCKED_V16, entry_z=2.0, exit_z=2.5).validate()


def test_stop_must_be_outside_entry():
    with pytest.raises(ValueError, match="stop_z"):
        replace(LOCKED_V16, entry_z=2.0, stop_z=1.5).validate()


def test_time_stop_is_mandatory():
    """Reversion without a time stop silently becomes a trend position."""
    with pytest.raises(ValueError, match="time stop"):
        replace(LOCKED_V16, max_holding_bars=0).validate()


@pytest.mark.parametrize("field,bad", [
    ("lookback", 1), ("atr_period", 1), ("pip", 0.0), ("contract_size", 0.0),
])
def test_invalid_config_is_rejected(field, bad):
    with pytest.raises(ValueError):
        replace(LOCKED_V16, **{field: bad}).validate()


def test_locked_defaults_are_valid():
    LOCKED_V16.validate()


# --- no look-ahead ----------------------------------------------------------


def test_bands_use_only_prior_bars():
    df = _bars([1.20 + i * 0.001 for i in range(60)])
    cfg = replace(LOCKED_V16, lookback=5, atr_period=4)
    out = add_bands(df, cfg)

    i = 30
    expected_mean = df["close"].iloc[i - 5:i].mean()
    assert out["mean"].iloc[i] == approx(expected_mean)
    # The z-score at bar i must be computed from bar i-1's close.
    expected_z = ((df["close"].iloc[i - 1] - expected_mean)
                  / df["close"].iloc[i - 5:i].std(ddof=0))
    assert out["z"].iloc[i] == approx(expected_z)


def test_a_future_spike_cannot_change_an_earlier_band():
    closes = [1.20 + 0.001 * np.sin(i / 3) for i in range(120)]
    df = _bars(closes)
    spiked = df.copy()
    spiked.loc[100, "close"] = 9.0
    cfg = replace(LOCKED_V16, lookback=5, atr_period=4)

    a = add_bands(df, cfg).iloc[:80]
    b = add_bands(spiked, cfg).iloc[:80]
    for col in ("mean", "sd", "z", "atr"):
        pd.testing.assert_series_equal(a[col], b[col])


def test_bands_are_nan_during_warmup():
    out = add_bands(_bars([1.2] * 30), LOCKED_V16)
    assert np.isnan(out["mean"].iloc[0])
    assert np.isnan(out["atr"].iloc[0])


# --- behaviour --------------------------------------------------------------


def test_flat_market_produces_no_trades():
    assert replay_v16(_bars([1.2000] * 200), LOCKED_V16, ZERO_COST).trades == []


def test_low_volatility_is_skipped():
    tiny = _bars([1.2000 + (i % 2) * 1e-6 for i in range(300)], pad=2e-6)
    assert replay_v16(tiny, LOCKED_V16, ZERO_COST).trades == []


def test_a_stretch_below_the_mean_is_bought():
    """Entry is against the move -- the defining difference from V15."""
    closes = [1.2000] * 40 + [1.1000] + [1.2000] * 40
    result = replay_v16(_bars(closes), LOCKED_V16, ZERO_COST)
    assert result.trades
    assert result.trades[0].side is Signal.BUY


def test_a_stretch_above_the_mean_is_sold():
    closes = [1.2000] * 40 + [1.3000] + [1.2000] * 40
    result = replay_v16(_bars(closes), LOCKED_V16, ZERO_COST)
    assert result.trades
    assert result.trades[0].side is Signal.SELL


def test_an_oscillating_market_is_profitable_gross():
    """Sanity check: the rules must work where the premise holds."""
    result = replay_v16(_oscillating(), LOCKED_V16, ZERO_COST)
    assert len(result.trades) > 3
    assert result.net_profit > 0


def test_time_stop_closes_a_stuck_position():
    cfg = replace(LOCKED_V16, max_holding_bars=5)
    # Stretch down, then trend away so neither reversion nor stop triggers
    # before the time stop does.
    closes = [1.2000] * 30 + [1.1500] + [1.1500 - i * 1e-5 for i in range(60)]
    result = replay_v16(_bars(closes), cfg, ZERO_COST)
    assert result.trades
    assert any(t.reason == "TIME" for t in result.trades)


def test_only_one_position_at_a_time():
    result = replay_v16(_oscillating(n=600), LOCKED_V16, ZERO_COST)
    for a, b in zip(result.trades, result.trades[1:]):
        assert b.entry_time >= a.exit_time


def test_open_position_is_booked_at_end_of_data():
    closes = [1.2000] * 40 + [1.1000] * 3
    result = replay_v16(_bars(closes), LOCKED_V16, ZERO_COST)
    assert result.trades
    assert result.trades[-1].reason in {"EOD", "REVERTED", "STOP", "TIME"}
    assert result.net_profit == approx(
        sum(t.profit for t in result.trades), abs=0.05)


def test_costs_reduce_the_result():
    free = replay_v16(_oscillating(), LOCKED_V16, ZERO_COST)
    paid = replay_v16(_oscillating(), LOCKED_V16, RETAIL_TYPICAL)
    assert len(paid.trades) == len(free.trades)
    assert paid.net_profit < free.net_profit
    assert paid.total_costs > 0


def test_instrument_override_rescales_correctly():
    gold_like = _bars([2000.0 + 20.0 * np.sin(i / 6) for i in range(400)],
                      pad=1.0)
    as_fx = replay_v16(gold_like, LOCKED_V16, ZERO_COST)
    as_gold = replay_v16(gold_like, LOCKED_V16, ZERO_COST,
                         instrument=instrument_for("XAUUSD"))
    assert as_fx.trades and as_gold.trades
    assert abs(as_fx.net_profit) > abs(as_gold.net_profit) * 10
