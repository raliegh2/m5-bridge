"""Instrument conventions, and the refusal to guess them."""

import pandas as pd
import pytest
from pytest import approx

from mt5_ai_bridge.candidate_v15 import LOCKED, replay
from mt5_ai_bridge.costs import ZERO_COST
from mt5_ai_bridge.instruments import (INSTRUMENTS, UNSUPPORTED, Instrument,
                                       instrument_for, is_supported)

from .test_candidate_v15 import _bars


def test_fx_majors_use_standard_conventions():
    for symbol in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"):
        inst = instrument_for(symbol)
        assert inst.pip == 0.0001
        assert inst.contract_size == 100_000
        assert inst.pip_value_per_lot == approx(10.0)


def test_gold_is_not_an_fx_major():
    """The bug this module exists to prevent: gold priced as if it were FX."""
    gold = instrument_for("XAUUSD")
    assert gold.pip == 0.01
    assert gold.contract_size == 100
    assert gold.pip_value_per_lot == approx(1.0)
    # A pip of gold is worth a tenth of a pip of EURUSD per lot, and a lot is
    # 1000x smaller in units. Neither number survives being assumed.
    assert instrument_for("EURUSD").pip_value_per_lot / gold.pip_value_per_lot \
        == approx(10.0)
    assert instrument_for("EURUSD").contract_size / gold.contract_size \
        == approx(1000.0)


def test_silver_conventions():
    silver = instrument_for("XAGUSD")
    assert silver.contract_size == 5_000
    assert silver.pip_value_per_lot == approx(50.0)


def test_lookup_is_case_insensitive():
    assert instrument_for("gbpusd") is instrument_for("GBPUSD")


@pytest.mark.parametrize("symbol", sorted(UNSUPPORTED))
def test_non_usd_quoted_symbols_are_refused_not_guessed(symbol):
    assert not is_supported(symbol)
    with pytest.raises(ValueError, match="not priceable here"):
        instrument_for(symbol)


def test_jpy_refusal_names_the_missing_piece():
    with pytest.raises(ValueError, match="USDJPY conversion"):
        instrument_for("USDJPY")


def test_unknown_symbol_is_refused_with_guidance():
    with pytest.raises(ValueError, match="unknown instrument"):
        instrument_for("BTCUSD")


def test_is_supported_matches_the_table():
    for symbol in INSTRUMENTS:
        assert is_supported(symbol)
    assert not is_supported("USDJPY")


# --- integration with the candidate ----------------------------------------


def _gold_bars(n=140):
    """Gold-like prices: ~2000, moving in dollars not pips."""
    closes = [2000.0 + i * 2.0 for i in range(n)]
    return pd.DataFrame({
        "time": [1_700_000_000 + i * 14_400 for i in range(n)],
        "open": closes,
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
    })


def test_instrument_override_changes_pnl_scale():
    """Same bars, same rules -- only the contract conventions differ."""
    bars = _gold_bars()
    as_fx = replay(bars, LOCKED, ZERO_COST)                     # wrong
    as_gold = replay(bars, LOCKED, ZERO_COST,
                     instrument=instrument_for("XAUUSD"))       # right

    assert as_fx.trades and as_gold.trades
    # The FX-convention run reports a wildly larger number for identical price
    # action. That divergence is exactly the silent bug.
    assert abs(as_fx.net_profit) > abs(as_gold.net_profit) * 10


def test_override_does_not_disturb_an_fx_major():
    bars = _bars([1.2000 + i * 0.0020 for i in range(120)])
    plain = replay(bars, LOCKED, ZERO_COST)
    explicit = replay(bars, LOCKED, ZERO_COST,
                      instrument=instrument_for("GBPUSD"))
    assert plain.net_profit == approx(explicit.net_profit)
    assert len(plain.trades) == len(explicit.trades)


def test_override_is_validated():
    bars = _bars([1.2000 + i * 0.0020 for i in range(60)])
    with pytest.raises(ValueError):
        replay(bars, LOCKED, ZERO_COST,
               instrument=Instrument("BAD", pip=0.0, contract_size=100.0))
