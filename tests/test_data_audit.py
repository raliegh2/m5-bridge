"""Data-quality audit: each defect built deliberately and then detected."""

import numpy as np
import pandas as pd
import pytest

from mt5_ai_bridge.data_audit import (INSTRUMENT_INCEPTION, audit_bars,
                                      inception_of)

H4 = 14_400
START = 1_100_000_000          # 2004-ish, after every inception date


def _clean(n=2000, start=START, base=1.2000, seed=0):
    """A well-formed series with genuine variation in every bar."""
    rng = np.random.default_rng(seed)
    closes = base + np.cumsum(rng.normal(0, 0.001, n))
    spread = np.abs(rng.normal(0.0008, 0.0002, n)) + 0.0002
    return pd.DataFrame({
        "time": [start + i * H4 for i in range(n)],
        "open": closes,
        "high": closes + spread,
        "low": closes - spread,
        "close": closes,
        "tick_volume": rng.integers(50, 5000, n),
    })


def _codes(result):
    return {i.code for i in result.issues}


# --- the happy path ---------------------------------------------------------


def test_clean_series_passes():
    r = audit_bars(_clean(), "GBPUSD", "H4", H4)
    assert r.usable
    assert r.verdict == "USABLE"
    assert not r.fatal
    assert r.trusted_from == r.start


def test_summary_is_serialisable():
    s = audit_bars(_clean(), "GBPUSD", "H4", H4).summary()
    assert s["symbol"] == "GBPUSD"
    assert s["verdict"] == "USABLE"
    assert isinstance(s["issues"], list)


# --- structural corruption --------------------------------------------------


def test_empty_frame_is_unusable():
    r = audit_bars(pd.DataFrame(), "X", "H4")
    assert not r.usable
    assert "empty" in _codes(r)


def test_missing_columns_are_fatal():
    r = audit_bars(pd.DataFrame({"time": [1], "open": [1.0]}), "X", "H4")
    assert not r.usable
    assert "columns" in _codes(r)


def test_non_positive_prices_are_fatal():
    df = _clean(500)
    df.loc[100, "low"] = -1.0
    r = audit_bars(df, "X", "H4", H4)
    assert not r.usable
    assert "bad_price" in _codes(r)


def test_high_below_low_is_fatal():
    df = _clean(500)
    df.loc[50, "high"] = df.loc[50, "low"] - 0.01
    r = audit_bars(df, "X", "H4", H4)
    assert not r.usable
    assert "high_below_low" in _codes(r)


def test_close_outside_the_bar_range_is_fatal():
    df = _clean(500)
    df.loc[75, "close"] = df.loc[75, "high"] + 0.01
    r = audit_bars(df, "X", "H4", H4)
    assert not r.usable
    assert "oc_outside_range" in _codes(r)


def test_unsorted_timestamps_are_fatal():
    df = _clean(500)
    df.loc[200, "time"] = int(df.loc[10, "time"])
    r = audit_bars(df, "X", "H4", H4)
    assert not r.usable
    assert "unsorted" in _codes(r)


def test_duplicate_timestamps_are_flagged():
    df = _clean(500)
    df.loc[300, "time"] = int(df.loc[299, "time"])
    r = audit_bars(df, "X", "H4", H4)
    assert "duplicate_time" in _codes(r)


# --- synthetic padding ------------------------------------------------------


def test_zero_range_bars_are_flagged():
    df = _clean(1000)
    for i in range(50):
        df.loc[i, "high"] = df.loc[i, "close"]
        df.loc[i, "low"] = df.loc[i, "close"]
    r = audit_bars(df, "X", "H4", H4)
    assert "zero_range" in _codes(r)


def test_zero_volume_bars_are_flagged():
    df = _clean(1000)
    df.loc[:100, "tick_volume"] = 0
    r = audit_bars(df, "X", "H4", H4)
    assert "zero_volume" in _codes(r)


def test_stale_close_run_is_flagged():
    df = _clean(1000)
    df.loc[400:450, "close"] = 1.2345
    r = audit_bars(df, "X", "H4", H4)
    assert "stale_run" in _codes(r)


def test_synthetic_prefix_sets_a_trusted_start():
    """The EURUSD failure mode: a padded prefix ahead of real data."""
    df = _clean(3000)
    # First 1000 bars fabricated: flat, no range, no volume.
    df.loc[:999, "high"] = df.loc[:999, "close"]
    df.loc[:999, "low"] = df.loc[:999, "close"]
    df.loc[:999, "tick_volume"] = 0

    r = audit_bars(df, "X", "H4", H4)
    assert r.verdict == "USABLE FROM"
    assert "synthetic_prefix" in _codes(r)
    assert r.trusted_from > r.start
    # Boundary should land near where the padding stops, not at bar 0.
    assert r.trusted_from >= int(df.loc[900, "time"])


def test_sporadic_zero_range_does_not_condemn_a_series():
    """Real thin-hour bars occasionally have no range; that is not fake data."""
    df = _clean(3000)
    for i in range(0, 3000, 300):
        df.loc[i, "high"] = df.loc[i, "close"]
        df.loc[i, "low"] = df.loc[i, "close"]
    r = audit_bars(df, "X", "H4", H4)
    assert "synthetic_prefix" not in _codes(r)
    assert r.trusted_from == r.start


# --- outliers and regime breaks --------------------------------------------


def test_extreme_return_is_flagged():
    df = _clean(2000)
    df.loc[1000, "close"] *= 1.5           # a 50% H4 move is a bad tick
    df.loc[1000, "high"] = df.loc[1000, "close"]
    r = audit_bars(df, "X", "H4", H4)
    assert "return_outliers" in _codes(r)


def test_volatility_regime_break_is_detected():
    """Two sources spliced together show up as a step change in volatility."""
    quiet = _clean(1500, seed=1)
    quiet["close"] = 1.2 + np.cumsum(
        np.random.default_rng(1).normal(0, 0.00005, 1500))
    loud = _clean(1500, start=START + 1500 * H4, seed=2)
    loud["close"] = 1.2 + np.cumsum(
        np.random.default_rng(2).normal(0, 0.005, 1500))
    for frame in (quiet, loud):
        frame["high"] = frame["close"] + 0.001
        frame["low"] = frame["close"] - 0.001
        frame["open"] = frame["close"]
    df = pd.concat([quiet, loud], ignore_index=True)

    r = audit_bars(df, "X", "H4", H4)
    assert "volatility_regime_break" in _codes(r)
    assert r.trusted_from > r.start        # trust only the later, real half


# --- inception dates --------------------------------------------------------


def test_eurusd_before_the_euro_is_rejected():
    """No statistic can catch a DEM series relabelled EURUSD -- this must."""
    df = _clean(3000, start=800_000_000)   # 1995, before the euro
    r = audit_bars(df, "EURUSD", "H4", H4)
    assert "pre_inception" in _codes(r)
    assert r.trusted_from == INSTRUMENT_INCEPTION["EURUSD"][0]
    assert r.verdict == "USABLE FROM"


def test_a_symbol_starting_after_inception_is_untouched():
    df = _clean(2000, start=1_400_000_000)  # 2014
    r = audit_bars(df, "EURUSD", "H4", H4)
    assert "pre_inception" not in _codes(r)
    assert r.trusted_from == r.start


def test_symbols_without_an_inception_date_are_unaffected():
    assert inception_of("GBPUSD") is None
    r = audit_bars(_clean(1000, start=700_000_000), "GBPUSD", "H4", H4)
    assert "pre_inception" not in _codes(r)


def test_inception_lookup_is_case_insensitive():
    assert inception_of("eurusd") == inception_of("EURUSD")


@pytest.mark.parametrize("symbol", sorted(INSTRUMENT_INCEPTION))
def test_every_inception_entry_has_a_reason(symbol):
    ts, why = INSTRUMENT_INCEPTION[symbol]
    assert ts > 0
    assert len(why) > 10, "an inception date must explain itself"
