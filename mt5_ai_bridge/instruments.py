"""Per-instrument pip size, contract size, and quote-currency conversion.

Applying one set of conventions to every symbol silently produces nonsense. A
pip is 0.0001 on EURUSD and 0.01 on USDJPY; a lot is 100,000 units of FX but
100 ounces of gold. Getting this wrong does not raise -- it just multiplies the
P&L by a hundred, which is indistinguishable from a spectacular strategy.

USD-quoted symbols
------------------
For EURUSD, XAUUSD and friends the P&L is already in account currency:

    profit_usd = (exit - entry) * lots * contract_size

Non-USD-quoted symbols
----------------------
A JPY-quoted pair earns **yen**, which has to be converted at the USDJPY rate
*at the time the trade closed* -- not at today's rate, and not at an average.
Over a 33-year backtest USDJPY ranges from roughly 75 to 160, so a fixed
conversion misstates P&L by up to a factor of two, with the error correlated
to the period being tested.

:class:`Converter` performs that conversion from a USDJPY series. A symbol
whose quote currency is not USD can only be priced when a converter is
supplied; without one, :func:`instrument_for` raises rather than guessing.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Optional, Sequence

__all__ = ["Instrument", "INSTRUMENTS", "instrument_for", "is_supported",
           "Converter", "quote_currency_of"]


@dataclass(frozen=True)
class Instrument:
    """Contract conventions for one symbol.

    ``quote`` is the currency the P&L is earned in. When it is not USD, a
    :class:`Converter` must be attached before the instrument can be priced.
    """

    symbol: str
    pip: float
    contract_size: float
    note: str = ""
    quote: str = "USD"
    converter: Optional["Converter"] = None

    @property
    def needs_conversion(self) -> bool:
        return self.quote != "USD"

    @property
    def pip_value_per_lot(self) -> float:
        """USD per pip per 1.0 lot. Only exact for USD-quoted symbols.

        For a converted instrument this is the *quote-currency* pip value; use
        :meth:`pip_value_per_lot_at` to get dollars at a point in time.
        """
        return self.pip * self.contract_size

    def pip_value_per_lot_at(self, when: int) -> float:
        """USD per pip per lot at ``when`` (epoch seconds)."""
        base = self.pip * self.contract_size
        if not self.needs_conversion:
            return base
        if self.converter is None:
            raise ValueError(
                f"{self.symbol} is quoted in {self.quote} and needs a "
                "converter to be priced in USD")
        return base * self.converter.usd_per_unit(when)

    def to_usd(self, amount_quote: float, when: int) -> float:
        """Convert a quote-currency amount to USD at ``when``."""
        if not self.needs_conversion:
            return amount_quote
        if self.converter is None:
            raise ValueError(
                f"{self.symbol} is quoted in {self.quote} and needs a "
                "converter to be priced in USD")
        return amount_quote * self.converter.usd_per_unit(when)

    def with_converter(self, converter: "Converter") -> "Instrument":
        from dataclasses import replace as _replace
        return _replace(self, converter=converter)


class Converter:
    """Quote-currency -> USD, using the rate in force at a point in time.

    Built from a USDJPY-style series (USD as the BASE currency), where one USD
    buys ``rate`` units of the quote currency, so one unit is worth
    ``1 / rate`` dollars.

    Lookup is as-of: the most recent rate at or before the requested time,
    never a later one. Using a future rate to value a past trade is look-ahead
    bias, and on a 33-year window it is a large one.
    """

    def __init__(self, times: Sequence[int], rates: Sequence[float],
                 name: str = "USDJPY") -> None:
        pairs = sorted((int(t), float(r)) for t, r in zip(times, rates)
                       if r and r == r and float(r) > 0)
        if not pairs:
            raise ValueError(f"{name} converter needs at least one rate")
        self.name = name
        self._times = [p[0] for p in pairs]
        self._rates = [p[1] for p in pairs]

    @classmethod
    def from_frame(cls, df, name: str = "USDJPY") -> "Converter":
        return cls(df["time"].tolist(), df["close"].tolist(), name)

    @property
    def start(self) -> int:
        return self._times[0]

    @property
    def end(self) -> int:
        return self._times[-1]

    def rate_at(self, when: int) -> float:
        """Quote units per USD at ``when`` (as-of, never forward-looking)."""
        idx = bisect_right(self._times, int(when)) - 1
        if idx < 0:
            # Before the series starts there is no honest rate; the earliest
            # known one is the least-wrong choice and is flagged by coverage().
            return self._rates[0]
        return self._rates[idx]

    def usd_per_unit(self, when: int) -> float:
        """USD value of one unit of the quote currency at ``when``."""
        rate = self.rate_at(when)
        if rate <= 0:
            raise ValueError(f"{self.name} rate at {when} is not positive")
        return 1.0 / rate

    def covers(self, when: int) -> bool:
        return self._times[0] <= int(when) <= self._times[-1]

    def coverage(self, times: Sequence[int]) -> float:
        """Fraction of ``times`` that fall inside the converter's range."""
        ts = list(times)
        if not ts:
            return 1.0
        return sum(1 for t in ts if self.covers(t)) / len(ts)


def quote_currency_of(symbol: str) -> str:
    s = str(symbol).upper()
    return s[3:6] if len(s) >= 6 else "USD"


INSTRUMENTS: dict[str, Instrument] = {
    "EURUSD": Instrument("EURUSD", 0.0001, 100_000),
    "GBPUSD": Instrument("GBPUSD", 0.0001, 100_000),
    "AUDUSD": Instrument("AUDUSD", 0.0001, 100_000),
    "NZDUSD": Instrument("NZDUSD", 0.0001, 100_000),
    "XAUUSD": Instrument("XAUUSD", 0.01, 100,
                         note="100 troy ounces; $1 per 0.01 move per lot"),
    "XAGUSD": Instrument("XAGUSD", 0.01, 5_000,
                         note="5,000 troy ounces"),
}

# Symbols priceable only when the matching converter is supplied.
CONVERTIBLE: dict[str, Instrument] = {
    "USDJPY": Instrument("USDJPY", 0.01, 100_000, quote="JPY"),
    "GBPJPY": Instrument("GBPJPY", 0.01, 100_000, quote="JPY"),
    "EURJPY": Instrument("EURJPY", 0.01, 100_000, quote="JPY"),
    "AUDJPY": Instrument("AUDJPY", 0.01, 100_000, quote="JPY"),
    "CHFJPY": Instrument("CHFJPY", 0.01, 100_000, quote="JPY"),
    "USDCHF": Instrument("USDCHF", 0.0001, 100_000, quote="CHF"),
    "USDCAD": Instrument("USDCAD", 0.0001, 100_000, quote="CAD"),
}

# Which series converts each quote currency to USD.
CONVERSION_SERIES: dict[str, str] = {
    "JPY": "USDJPY",
    "CHF": "USDCHF",
    "CAD": "USDCAD",
}


def is_supported(symbol: str, converter: Optional[Converter] = None) -> bool:
    key = str(symbol).upper()
    if key in INSTRUMENTS:
        return True
    return key in CONVERTIBLE and converter is not None


def conversion_series_for(symbol: str) -> Optional[str]:
    """Which price series is needed to state this symbol's P&L in USD."""
    key = str(symbol).upper()
    inst = CONVERTIBLE.get(key)
    return CONVERSION_SERIES.get(inst.quote) if inst else None


def instrument_for(symbol: str,
                   converter: Optional[Converter] = None) -> Instrument:
    """Look up conventions, refusing symbols we cannot price correctly.

    A non-USD-quoted symbol requires ``converter``; without one it raises
    rather than falling back to FX-major assumptions.
    """
    key = str(symbol).upper()
    if key in INSTRUMENTS:
        return INSTRUMENTS[key]
    if key in CONVERTIBLE:
        inst = CONVERTIBLE[key]
        if converter is None:
            needed = CONVERSION_SERIES.get(inst.quote, f"USD{inst.quote}")
            raise ValueError(
                f"{key} is quoted in {inst.quote}; pass a Converter built "
                f"from {needed} to state its P&L in USD. Without one the "
                "result would be off by the exchange rate.")
        return inst.with_converter(converter)
    raise ValueError(
        f"unknown instrument {key}; add it to instruments.py with its real "
        "pip and contract size. Guessing produces P&L that is wrong by a "
        "factor of 100 without failing.")
