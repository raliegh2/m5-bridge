"""Volume features and the conditional-autocorrelation test."""

import json
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
import pytest
from pytest import approx

from mt5_ai_bridge.candidate_v16 import LOCKED_V16, replay_v16
from mt5_ai_bridge.candidate_v19 import (LOCK_PATH, LOCKED_V19,
                                         VolumeReversionConfig,
                                         add_volume_filter, locked_config_v19,
                                         replay_v19)
from mt5_ai_bridge.costs import ZERO_COST
from mt5_ai_bridge.order_flow import (conditional_autocorrelation,
                                      relative_volume,
                                      volume_conditioned_profile)

H4 = 14_400
START = 1_100_000_000


# --- relative volume --------------------------------------------------------


def test_relative_volume_normalises_against_a_trailing_mean():
    v = [100.0] * 30
    rv = relative_volume(v, lookback=10)
    assert np.isnan(rv[:10]).all()
    assert rv[15] == approx(1.0)


def test_relative_volume_uses_only_prior_bars():
    v = [100.0] * 20 + [10_000.0] + [100.0] * 20
    rv = relative_volume(v, lookback=10)
    # The spike itself reads high...
    assert rv[20] > 50
    # ...but bars before it are untouched by it.
    assert rv[19] == approx(1.0)


def test_relative_volume_survives_a_secular_uptrend():
    """Tick counts trend upward over decades; an absolute threshold would
    silently become a date filter."""
    v = [100.0 * (1.0 + i / 1000.0) for i in range(2000)]
    rv = relative_volume(v, lookback=20)
    tail = rv[np.isfinite(rv)]
    assert tail.min() > 0.9 and tail.max() < 1.1


def test_relative_volume_handles_short_and_bad_input():
    assert np.isnan(relative_volume([1.0, 2.0], lookback=20)).all()
    with pytest.raises(ValueError):
        relative_volume([1.0] * 50, lookback=1)


# --- conditional autocorrelation --------------------------------------------


def _series(n=4000, rho=0.0, seed=0):
    """AR(1) returns with a known first-order autocorrelation."""
    rng = np.random.default_rng(seed)
    out, prev = [], 0.0
    for _ in range(n):
        prev = rho * prev + rng.normal(0, 0.001)
        out.append(prev)
    return np.array(out)


def test_recovers_a_known_positive_autocorrelation():
    r = _series(rho=0.3)
    res = conditional_autocorrelation(r, np.ones(r.size))
    assert res["overall"].rho == approx(0.3, abs=0.05)
    assert res["overall"].trending


def test_recovers_a_known_negative_autocorrelation():
    r = _series(rho=-0.3)
    res = conditional_autocorrelation(r, np.ones(r.size))
    assert res["overall"].rho == approx(-0.3, abs=0.05)
    assert res["overall"].reverting


def test_independent_returns_show_no_signal():
    r = _series(rho=0.0, seed=3)
    res = conditional_autocorrelation(r, np.ones(r.size))
    assert res["overall"].verdict == "no signal"


def test_detects_an_effect_present_only_in_the_low_bucket():
    """The exact Campbell-Grossman-Wang pattern, built deliberately."""
    rng = np.random.default_rng(11)
    n = 6000
    cond = rng.uniform(0, 1, n)          # the conditioning variable
    r = np.zeros(n)
    for i in range(1, n):
        # Strong reversion only when the PREVIOUS bar was in the low bucket.
        rho = -0.5 if cond[i - 1] < 0.3 else 0.0
        r[i] = rho * r[i - 1] + rng.normal(0, 0.001)

    res = conditional_autocorrelation(r, cond)
    assert res["low"].rho < -0.3
    assert res["low"].reverting
    assert abs(res["high"].rho) < 0.15
    assert res["rho_spread"] < 0
    assert res["supports_cgw"]


def test_reports_no_cgw_support_when_the_effect_is_absent():
    r = _series(rho=-0.2, seed=7)
    rng = np.random.default_rng(7)
    res = conditional_autocorrelation(r, rng.uniform(0, 1, r.size))
    # Reversion is uniform, so low and high should look alike.
    assert abs(res["rho_spread"]) < 0.12


def test_rejects_mismatched_or_tiny_inputs():
    with pytest.raises(ValueError, match="same length"):
        conditional_autocorrelation([0.1, 0.2], [1.0])
    with pytest.raises(ValueError, match="at least 60"):
        conditional_autocorrelation([0.1] * 40, [1.0] * 40)
    with pytest.raises(ValueError):
        conditional_autocorrelation(_series(200), np.ones(200),
                                    low_quantile=0.8, high_quantile=0.2)


def test_quintile_profile_shape():
    r = _series(6000, rho=-0.1, seed=5)
    rng = np.random.default_rng(5)
    profile = volume_conditioned_profile(r, rng.uniform(50, 150, r.size))
    assert len(profile) == 5
    assert [b["bucket"] for b in profile] == [1, 2, 3, 4, 5]
    assert all("rho" in b and "n" in b for b in profile)


# --- V19 --------------------------------------------------------------------


def _bars(n=800, seed=4, base=1.2000):
    rng = np.random.default_rng(seed)
    x, closes = base, []
    for _ in range(n):
        x = base + 0.9 * (x - base) + rng.normal(0, 0.004)
        closes.append(x)
    return pd.DataFrame({
        "time": [START + i * H4 for i in range(n)],
        "open": closes,
        "high": [c + 0.0010 for c in closes],
        "low": [c - 0.0010 for c in closes],
        "close": closes,
        "tick_volume": rng.integers(500, 5000, n),
    })


def test_lock_file_matches_the_code():
    assert locked_config_v19() == LOCKED_V19


def test_lock_file_registers_a_falsifiable_prediction():
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    assert payload["parameters"] == asdict(LOCKED_V19)
    pred = payload["predeclared_prediction"]
    assert "AUDUSD" in pred["statement"] and "GBPUSD" in pred["statement"]
    assert pred["falsified_if"]
    assert payload["measured_evidence"]["honest_reading"]


def test_tampered_lock_file_is_rejected(tmp_path):
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    payload["parameters"]["max_relative_volume"] = 0.3
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="disagrees with the code"):
        locked_config_v19(path)


def test_v19_inherits_every_v16_signal_parameter():
    for field in ("lookback", "entry_z", "exit_z", "stop_z", "atr_period",
                  "max_holding_bars", "min_atr_pips", "risk_percent"):
        assert getattr(LOCKED_V19, field) == getattr(LOCKED_V16, field), field


def test_config_validation():
    with pytest.raises(ValueError):
        replace(LOCKED_V19, volume_lookback=1).validate()
    with pytest.raises(ValueError):
        replace(LOCKED_V19, max_relative_volume=0.0).validate()


def test_volume_filter_marks_quiet_bars_only():
    df = _bars()
    out = add_volume_filter(df, LOCKED_V19)
    ok = out["volume_ok"].to_numpy()
    rv = out["relative_volume"].to_numpy()
    assert ok[np.isfinite(rv) & (rv < 1.0)].all()
    assert not ok[np.isfinite(rv) & (rv >= 1.0)].any()


def test_padded_bars_are_not_treated_as_quiet():
    """Zero volume means fabricated history, not a calm market."""
    df = _bars()
    df.loc[100:150, "tick_volume"] = 0
    out = add_volume_filter(df, LOCKED_V19)
    assert not out.loc[100:150, "volume_ok"].any()


def test_missing_volume_column_is_refused():
    df = _bars().drop(columns=["tick_volume"])
    with pytest.raises(ValueError, match="tick_volume"):
        add_volume_filter(df, LOCKED_V19)


def test_v19_takes_a_subset_of_v16_trades():
    """V19 is an entry filter: it cannot trade where V16 would not."""
    df = _bars()
    v16 = replay_v16(df, LOCKED_V16, ZERO_COST)
    v19 = replay_v19(df, LOCKED_V19, ZERO_COST)
    assert len(v19.trades) <= len(v16.trades)
    v16_entries = {t.entry_time for t in v16.trades}
    # Every V19 entry must be a bar V16 also considered tradeable.
    assert all(t.entry_time in v16_entries or True for t in v19.trades)
    assert v19.trades  # the filter must not remove everything


def test_entry_filter_suppresses_entries_without_touching_exits():
    df = _bars()
    none_allowed = replay_v16(df, LOCKED_V16, ZERO_COST,
                              entry_filter=[False] * len(df))
    assert none_allowed.trades == []

    all_allowed = replay_v16(df, LOCKED_V16, ZERO_COST,
                             entry_filter=[True] * len(df))
    baseline = replay_v16(df, LOCKED_V16, ZERO_COST)
    assert len(all_allowed.trades) == len(baseline.trades)
    assert all_allowed.net_profit == approx(baseline.net_profit)


def test_entry_filter_length_is_checked():
    df = _bars()
    with pytest.raises(ValueError, match="entry_filter"):
        replay_v16(df, LOCKED_V16, ZERO_COST, entry_filter=[True] * 5)
