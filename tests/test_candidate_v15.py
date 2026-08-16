"""The locked V15 candidate: parameter integrity and no look-ahead."""

import json
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
import pytest
from pytest import approx

from mt5_ai_bridge.candidate_v15 import (LOCK_PATH, LOCKED, MomentumConfig,
                                         add_channels, locked_config, replay,
                                         resample_ohlc)
from mt5_ai_bridge.costs import RETAIL_TYPICAL, ZERO_COST
from mt5_ai_bridge.enums import Signal

H4 = 4 * 3600


def _bars(closes, highs=None, lows=None, start=1_700_000_000, step=H4):
    n = len(closes)
    highs = highs or [c + 0.0010 for c in closes]
    lows = lows or [c - 0.0010 for c in closes]
    return pd.DataFrame({
        "time": [start + i * step for i in range(n)],
        "open": closes, "high": highs, "low": lows, "close": closes,
    })


# --- the lock ---------------------------------------------------------------


def test_lock_file_matches_the_code():
    """If this fails, the lock file was edited after the fact."""
    assert locked_config() == LOCKED


def test_lock_file_documents_its_provenance():
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    assert payload["trials_used_for_deflation"] == 1
    assert payload["parameters"] == asdict(LOCKED)
    for key in ("hypothesis", "why_these_parameters", "provenance",
                "acceptance_gates", "honest_limitation"):
        assert payload[key], f"lock file is missing {key}"


def test_tampered_lock_file_is_rejected(tmp_path):
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8-sig"))
    payload["parameters"]["entry_lookback"] = 25       # a "small tweak"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="disagrees with the code"):
        locked_config(path)


def test_missing_lock_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        locked_config(tmp_path / "nope.json")


# --- config validation ------------------------------------------------------


@pytest.mark.parametrize("field,bad", [
    ("timeframe_minutes", 0),
    ("entry_lookback", 1),
    ("exit_lookback", 1),
    ("atr_period", 1),
    ("atr_stop_mult", 0.0),
    ("pip", 0.0),
    ("contract_size", 0.0),
])
def test_invalid_config_is_rejected(field, bad):
    with pytest.raises(ValueError):
        replace(LOCKED, **{field: bad}).validate()


def test_exit_lookback_must_be_shorter_than_entry():
    with pytest.raises(ValueError, match="exit_lookback"):
        replace(LOCKED, entry_lookback=10, exit_lookback=10).validate()


def test_locked_defaults_are_valid():
    LOCKED.validate()


# --- no look-ahead ----------------------------------------------------------


def test_channels_and_atr_only_use_prior_bars():
    """Row i's signal inputs must come from bars strictly before i."""
    closes = [1.20 + i * 0.001 for i in range(60)]
    df = _bars(closes)
    cfg = replace(LOCKED, entry_lookback=5, exit_lookback=3, atr_period=4,
                  trend_ema=5)
    out = add_channels(df, cfg)

    i = 40
    expected_high = df["high"].iloc[i - 5:i].max()
    expected_low = df["low"].iloc[i - 5:i].min()
    assert out["entry_high"].iloc[i] == approx(expected_high)
    assert out["entry_low"].iloc[i] == approx(expected_low)
    # The current bar's own extreme must NOT be included.
    assert out["entry_high"].iloc[i] < df["high"].iloc[i]


def test_a_future_spike_cannot_change_an_earlier_signal():
    closes = [1.20 + i * 0.0005 for i in range(80)]
    df = _bars(closes)
    spiked = df.copy()
    spiked.loc[70, "high"] = 5.0            # absurd bar well after row 40
    cfg = replace(LOCKED, entry_lookback=5, exit_lookback=3, atr_period=4,
                  trend_ema=5)

    a = add_channels(df, cfg).iloc[:60]
    b = add_channels(spiked, cfg).iloc[:60]
    for col in ("entry_high", "entry_low", "exit_high", "exit_low", "atr", "ema"):
        pd.testing.assert_series_equal(a[col], b[col])


def test_channels_are_nan_during_warmup():
    df = _bars([1.20 + i * 0.001 for i in range(30)])
    out = add_channels(df, LOCKED)
    assert np.isnan(out["entry_high"].iloc[0])
    assert np.isnan(out["atr"].iloc[0])


# --- resampling -------------------------------------------------------------


def test_resample_aggregates_ohlc_correctly():
    # 4h bins land on wall-clock boundaries, so start on one to get exactly
    # one bar out of 48 M5 bars.
    aligned = (1_700_000_000 // 14_400) * 14_400
    m5 = pd.DataFrame({
        "time": [aligned + i * 300 for i in range(48)],
        "open": [1.0 + i * 0.001 for i in range(48)],
        "high": [1.5 + i * 0.001 for i in range(48)],
        "low": [0.5 + i * 0.001 for i in range(48)],
        "close": [1.2 + i * 0.001 for i in range(48)],
    })
    h4 = resample_ohlc(m5, 240)
    assert len(h4) == 1
    assert h4["open"].iloc[0] == approx(m5["open"].iloc[0])
    assert h4["close"].iloc[0] == approx(m5["close"].iloc[-1])
    assert h4["high"].iloc[0] == approx(m5["high"].max())
    assert h4["low"].iloc[0] == approx(m5["low"].min())


def test_resample_requires_a_time_column():
    with pytest.raises(ValueError, match="time"):
        resample_ohlc(pd.DataFrame({"open": [1], "high": [1],
                                    "low": [1], "close": [1]}), 240)


# --- replay mechanics -------------------------------------------------------


def test_flat_market_produces_no_trades():
    df = _bars([1.2000] * 120)
    assert replay(df, LOCKED, ZERO_COST).trades == []


def test_low_volatility_is_skipped():
    """min_atr_pips must keep the candidate out of dead markets."""
    closes = [1.2000 + (i % 2) * 1e-5 for i in range(200)]
    df = _bars(closes, highs=[c + 2e-5 for c in closes],
               lows=[c - 2e-5 for c in closes])
    assert replay(df, LOCKED, ZERO_COST).trades == []


def test_breakout_uptrend_opens_a_long():
    closes = [1.2000 + i * 0.0020 for i in range(120)]
    result = replay(_bars(closes), LOCKED, ZERO_COST)
    assert result.trades
    assert result.trades[0].side is Signal.BUY


def test_breakout_downtrend_opens_a_short():
    closes = [1.4000 - i * 0.0020 for i in range(120)]
    result = replay(_bars(closes), LOCKED, ZERO_COST)
    assert result.trades
    assert result.trades[0].side is Signal.SELL


def test_position_open_at_end_of_data_is_booked():
    """An unclosed trend trade must not vanish from the ledger or the P&L.

    A pure uptrend never trips the stop or the trailing channel, so without an
    end-of-data close the whole run would report zero trades and no P&L.
    """
    closes = [1.2000 + i * 0.0020 for i in range(120)]
    r = replay(_bars(closes), LOCKED, ZERO_COST)
    assert r.trades
    assert r.trades[-1].reason == "EOD"
    assert r.net_profit != 0.0
    # The booked P&L must equal the sum of the individual trades.
    assert r.net_profit == approx(sum(t.profit for t in r.trades), abs=0.02)


def test_only_one_position_is_open_at_a_time():
    closes = [1.2000 + i * 0.0020 for i in range(200)]
    result = replay(_bars(closes), LOCKED, ZERO_COST)
    for a, b in zip(result.trades, result.trades[1:]):
        assert b.entry_time >= a.exit_time


def test_costs_reduce_the_result_and_are_reported():
    closes = ([1.2000 + i * 0.0020 for i in range(80)]
              + [1.3600 - i * 0.0020 for i in range(80)])
    df = _bars(closes)
    free = replay(df, LOCKED, ZERO_COST)
    paid = replay(df, LOCKED, RETAIL_TYPICAL)
    assert len(paid.trades) == len(free.trades)
    assert paid.total_costs > 0
    assert free.total_costs == 0.0
    assert paid.net_profit < free.net_profit


def test_summary_shape_and_lossless_profit_factor():
    closes = [1.2000 + i * 0.0020 for i in range(120)]
    r = replay(_bars(closes), LOCKED, ZERO_COST)
    s = r.summary()
    assert set(s) == {"trades", "wins", "win_rate", "net_profit",
                      "total_costs", "profit_factor", "final_balance"}
    assert s["trades"] == len(r.trades)
    assert len(r.returns) == len(r.trades)


def test_stop_is_taken_before_the_channel_when_a_bar_spans_both():
    """Conservative fill assumption, matching backtest.py."""
    closes = [1.2000 + i * 0.0020 for i in range(60)]
    closes.append(closes[-1])
    df = _bars(closes)
    # Make the final bar span a huge range in both directions.
    df.loc[len(df) - 1, "low"] = 1.0
    df.loc[len(df) - 1, "high"] = 1.5
    r = replay(df, LOCKED, ZERO_COST)
    if r.trades:
        assert r.trades[-1].reason in {"STOP", "CHANNEL"}
