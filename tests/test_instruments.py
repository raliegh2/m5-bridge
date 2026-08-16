"""Instrument conventions, and the refusal to guess them."""

import pandas as pd
import pytest
from pytest import approx

from mt5_ai_bridge.candidate_v15 import LOCKED, replay
from mt5_ai_bridge.costs import ZERO_COST
from mt5_ai_bridge.instruments import (CONVERTIBLE, INSTRUMENTS, Converter,
                                       Instrument, conversion_series_for,
                                       instrument_for, is_supported,
                                       quote_currency_of)

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


@pytest.mark.parametrize("symbol", sorted(CONVERTIBLE))
def test_non_usd_quoted_symbols_are_refused_without_a_converter(symbol):
    assert not is_supported(symbol)
    with pytest.raises(ValueError, match="quoted in"):
        instrument_for(symbol)


def test_jpy_refusal_names_the_series_required():
    with pytest.raises(ValueError, match="USDJPY"):
        instrument_for("GBPJPY")
    assert conversion_series_for("GBPJPY") == "USDJPY"
    assert conversion_series_for("EURUSD") is None


def test_unknown_symbol_is_refused_with_guidance():
    with pytest.raises(ValueError, match="unknown instrument"):
        instrument_for("BTCUSD")


def test_is_supported_matches_the_table():
    for symbol in INSTRUMENTS:
        assert is_supported(symbol)
    assert not is_supported("USDJPY")
    assert is_supported("USDJPY", _usdjpy())


# --- quote-currency conversion ----------------------------------------------


def _usdjpy(rates=((1_000_000_000, 100.0), (2_000_000_000, 150.0))):
    return Converter([t for t, _ in rates], [r for _, r in rates], "USDJPY")


def test_quote_currency_parsing():
    assert quote_currency_of("GBPJPY") == "JPY"
    assert quote_currency_of("EURUSD") == "USD"


def test_converter_uses_the_rate_in_force_not_a_later_one():
    """Valuing a 2001 trade at a 2033 rate is look-ahead bias."""
    conv = _usdjpy()
    assert conv.rate_at(1_500_000_000) == 100.0      # between the two points
    assert conv.rate_at(2_000_000_000) == 150.0      # exactly on the later one
    assert conv.rate_at(2_500_000_000) == 150.0      # after the end, carry last


def test_converter_before_the_series_starts_uses_the_earliest_rate():
    conv = _usdjpy()
    assert conv.rate_at(1) == 100.0
    assert not conv.covers(1)
    assert conv.coverage([1, 1_500_000_000]) == 0.5


def test_usd_per_unit_is_the_reciprocal():
    conv = _usdjpy()
    assert conv.usd_per_unit(1_500_000_000) == approx(1 / 100.0)
    assert conv.usd_per_unit(2_000_000_000) == approx(1 / 150.0)


def test_converter_rejects_an_empty_series():
    with pytest.raises(ValueError, match="at least one rate"):
        Converter([], [])


def test_converter_ignores_non_positive_rates():
    conv = Converter([1, 2, 3], [0.0, 120.0, -5.0])
    assert conv.rate_at(3) == 120.0


def test_jpy_pair_prices_in_usd_with_a_converter():
    inst = instrument_for("GBPJPY", _usdjpy())
    assert inst.needs_conversion
    assert inst.quote == "JPY"
    # 1 pip (0.01) on 100,000 units = 1000 JPY; at 100/USD that is $10.
    assert inst.pip_value_per_lot_at(1_500_000_000) == approx(10.0)
    # At 150/USD the same pip is worth less.
    assert inst.pip_value_per_lot_at(2_000_000_000) == approx(1000 / 150.0)


def test_conversion_rate_materially_changes_pnl():
    """USDJPY ranged 75-160 historically; a fixed rate is not an approximation."""
    inst = instrument_for("USDJPY", _usdjpy())
    cheap = inst.to_usd(150_000.0, 1_500_000_000)     # at 100
    dear = inst.to_usd(150_000.0, 2_000_000_000)      # at 150
    assert cheap == approx(1500.0)
    assert dear == approx(1000.0)
    assert cheap / dear == approx(1.5)


def test_usd_quoted_instrument_ignores_conversion():
    inst = instrument_for("EURUSD")
    assert not inst.needs_conversion
    assert inst.to_usd(123.45, 1_500_000_000) == approx(123.45)
    assert inst.pip_value_per_lot_at(1) == approx(10.0)


def test_converted_instrument_without_a_converter_raises_on_use():
    bare = CONVERTIBLE["GBPJPY"]
    with pytest.raises(ValueError, match="needs a converter"):
        bare.to_usd(100.0, 1_500_000_000)
    with pytest.raises(ValueError, match="needs a converter"):
        bare.pip_value_per_lot_at(1_500_000_000)


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
