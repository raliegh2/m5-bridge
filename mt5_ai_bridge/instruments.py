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
           "Converter", "quote_currency_of", "cost_for", "settle"]


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
    # Typical retail spread in THIS instrument's pips, as (tight, typical,
    # wide). A pip is not a universal unit: 0.9 pips is a normal EURUSD spread
    # and an absurd one for gold, where a pip is $0.01 and the real spread is
    # nearer 30 of them. Applying one number to every symbol understates gold's
    # cost by ~20x and makes it look like the only profitable instrument.
    spread_tiers: tuple = (0.4, 0.9, 1.8)
    # Commission per lot per round turn, or None to take the preset's figure.
    # The $7/lot ECN convention is an FX arrangement. Index CFDs are priced
    # spread-only, and charging them $7 a lot is not conservatism -- it is
    # wrong, and badly so, because an index lot is ~1.0 where an FX lot is
    # ~0.01. Left unset it made the "tight" tier cost more than "wide".
    commission_per_lot: Optional[float] = None

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
    """Currency a symbol's P&L is earned in.

    Slicing characters 3-6 works for FX pairs and is meaningless for anything
    else, so the instrument tables are consulted first. "US30"[3:6] would
    otherwise return "0", and DE40 would look USD-quoted.
    """
    key = str(symbol).upper()
    inst = INSTRUMENTS.get(key) or CONVERTIBLE.get(key)
    if inst is not None:
        return inst.quote
    return key[3:6] if len(key) >= 6 else "USD"


_TIER_INDEX = {"tight": 0, "typical": 1, "wide": 2}


def cost_for(symbol: str, tier: str = "typical", base=None):
    """A :class:`~mt5_ai_bridge.costs.CostModel` scaled to this instrument.

    Takes the slippage/commission/swap structure from ``base`` (default:
    the named preset) but replaces the spread with the symbol's own typical
    figure, expressed in that symbol's pips. Slippage scales with it.
    """
    from .costs import PRESETS, CostModel

    key = str(symbol).upper()
    inst = INSTRUMENTS.get(key) or CONVERTIBLE.get(key)
    base = base if base is not None else PRESETS[str(tier).lower()]
    if inst is None or str(tier).lower() == "zero":
        return base

    idx = _TIER_INDEX.get(str(tier).lower())
    if idx is None:
        return base
    spread = float(inst.spread_tiers[idx])
    # Slippage keeps its proportion to the spread rather than staying fixed at
    # an FX-sized number.
    fx_reference = INSTRUMENTS["EURUSD"].spread_tiers[idx]
    scale = spread / fx_reference if fx_reference else 1.0
    commission = (base.commission_per_lot_round_turn
                  if inst.commission_per_lot is None
                  else float(inst.commission_per_lot))
    return CostModel(
        spread_pips=spread,
        slippage_pips=base.slippage_pips * scale,
        commission_per_lot_round_turn=commission,
        swap_pips_per_night_long=base.swap_pips_per_night_long * scale,
        swap_pips_per_night_short=base.swap_pips_per_night_short * scale,
    )


def settle(inst: Instrument, side, lots: float, entry: float,
           exit_price: float, nights: int, cost, when: int
           ) -> tuple[float, float]:
    """(gross USD, total cost USD) for one closed round turn.

    The single place trade settlement is defined, so every replay engine
    agrees. Spread, slippage and swap are incurred in the **quote currency**
    alongside the P&L and convert with it at the exit-time rate. Commission is
    quoted in USD per lot and must NOT be converted -- mixing the two is a
    silent error worth the exchange rate itself.
    """
    is_buy = (side.value == "BUY" if hasattr(side, "value")
              else str(side).upper().endswith("BUY"))
    direction = 1.0 if is_buy else -1.0
    pip_value_quote = inst.pip_value_per_lot

    gross_quote = direction * (exit_price - entry) * lots * inst.contract_size
    cost_quote = (cost.round_trip_pips * lots * pip_value_quote
                  + cost.swap_cost(side, lots, nights, pip_value_quote))

    gross_usd = inst.to_usd(gross_quote, when)
    cost_usd = inst.to_usd(cost_quote, when) + cost.commission_cost(lots)
    return gross_usd, cost_usd


INSTRUMENTS: dict[str, Instrument] = {
    "EURUSD": Instrument("EURUSD", 0.0001, 100_000),
    "GBPUSD": Instrument("GBPUSD", 0.0001, 100_000),
    "AUDUSD": Instrument("AUDUSD", 0.0001, 100_000),
    "NZDUSD": Instrument("NZDUSD", 0.0001, 100_000),
    # Gold: a "pip" of 0.01 is one cent, and retail spreads run 15-60 cents.
    "XAUUSD": Instrument("XAUUSD", 0.01, 100,
                         note="100 troy ounces; $1 per 0.01 move per lot",
                         spread_tiers=(15.0, 30.0, 60.0)),
    "XAGUSD": Instrument("XAGUSD", 0.01, 5_000,
                         note="5,000 troy ounces",
                         spread_tiers=(2.0, 4.0, 8.0)),

    # USD-quoted equity indices. The natural unit is one index point, not the
    # broker's 0.01 tick, and the contract is 1 unit -- so a one-point move on
    # one lot is one dollar. Spread tiers are the medians measured from this
    # broker's own history (research/data/*_H4.csv spread column) rather than
    # assumed.
    "US30": Instrument("US30", 1.0, 1.0, note="Dow, $1 per index point",
                       spread_tiers=(1.0, 1.5, 3.0), commission_per_lot=0.0),
    "US500": Instrument("US500", 1.0, 1.0, note="S&P, $1 per index point",
                        spread_tiers=(0.3, 0.5, 1.0), commission_per_lot=0.0),
    "USTEC": Instrument("USTEC", 1.0, 1.0, note="Nasdaq, $1 per index point",
                        spread_tiers=(0.7, 1.0, 2.0), commission_per_lot=0.0),
    "US2000": Instrument("US2000", 1.0, 1.0,
                         note="Russell, $1 per index point",
                         spread_tiers=(0.15, 0.24, 0.5),
                         commission_per_lot=0.0),

    # US-listed ETFs. One lot is one share, quoted in USD, so a $0.01 move on
    # one share is one cent. Spread tiers are in cents: liquid US ETFs quote a
    # 1-3 cent spread, and these are all among the most heavily traded.
    # Unlike the index CFDs above, these are FULLY TRADABLE on this account --
    # every one of the 26 index CFDs is trade_mode=DISABLED.
    "ONEQ": Instrument("ONEQ", 0.01, 1.0, note="Fidelity Nasdaq Composite ETF",
                       spread_tiers=(1.0, 3.0, 8.0), commission_per_lot=0.0),
    "IVV": Instrument("IVV", 0.01, 1.0, note="iShares S&P 500 ETF",
                      spread_tiers=(1.0, 2.0, 5.0), commission_per_lot=0.0),
    "IWM": Instrument("IWM", 0.01, 1.0, note="iShares Russell 2000 ETF",
                      spread_tiers=(1.0, 2.0, 5.0), commission_per_lot=0.0),
    "VTI": Instrument("VTI", 0.01, 1.0, note="Vanguard Total Market ETF",
                      spread_tiers=(1.0, 2.0, 5.0), commission_per_lot=0.0),
    "TQQQ": Instrument("TQQQ", 0.01, 1.0, note="3x leveraged Nasdaq 100",
                       spread_tiers=(1.0, 2.0, 5.0), commission_per_lot=0.0),
    "EEM": Instrument("EEM", 0.01, 1.0, note="iShares Emerging Markets ETF",
                      spread_tiers=(1.0, 2.0, 5.0), commission_per_lot=0.0),
}

# Symbols priceable only when the matching converter is supplied.
CONVERTIBLE: dict[str, Instrument] = {
    "USDJPY": Instrument("USDJPY", 0.01, 100_000, quote="JPY"),
    "GBPJPY": Instrument("GBPJPY", 0.01, 100_000, quote="JPY",
                         spread_tiers=(1.0, 2.0, 4.0)),
    "EURJPY": Instrument("EURJPY", 0.01, 100_000, quote="JPY"),
    "AUDJPY": Instrument("AUDJPY", 0.01, 100_000, quote="JPY"),
    "CHFJPY": Instrument("CHFJPY", 0.01, 100_000, quote="JPY"),
    "USDCHF": Instrument("USDCHF", 0.0001, 100_000, quote="CHF"),
    "USDCAD": Instrument("USDCAD", 0.0001, 100_000, quote="CAD"),

    # Indices quoted in their home currency. Priceable only with the matching
    # converter; guessing would repeat the gold mistake on a larger scale,
    # since JPN225 carries a 100-unit contract on a ~68,000 quote.
    "DE40": Instrument("DE40", 1.0, 1.0, quote="EUR",
                       spread_tiers=(0.7, 1.0, 2.0)),
    "FRA40": Instrument("FRA40", 0.1, 1.0, quote="EUR",
                        spread_tiers=(0.8, 1.1, 2.5)),
    "EUSTX50": Instrument("EUSTX50", 0.1, 1.0, quote="EUR",
                          spread_tiers=(0.4, 0.6, 1.5)),
    "UK100": Instrument("UK100", 0.1, 10.0, quote="GBP",
                        spread_tiers=(0.7, 1.0, 2.2)),
    "JPN225": Instrument("JPN225", 1.0, 100.0, quote="JPY",
                         spread_tiers=(3.0, 5.0, 8.0)),
    "AUS200": Instrument("AUS200", 0.1, 10.0, quote="AUD",
                         spread_tiers=(0.8, 1.2, 1.9)),
    "HK50": Instrument("HK50", 1.0, 1.0, quote="HKD",
                       spread_tiers=(0.5, 0.7, 0.9)),
    "SWI20": Instrument("SWI20", 1.0, 1.0, quote="CHF",
                        spread_tiers=(2.0, 3.0, 5.0)),
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
