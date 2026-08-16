"""Per-instrument pip size and contract size, and what we refuse to price.

Applying one set of conventions to every symbol silently produces nonsense. A
pip is 0.0001 on EURUSD and 0.01 on USDJPY; a lot is 100,000 units of FX but
100 ounces of gold. Getting this wrong does not raise -- it just multiplies the
P&L by a hundred, which is indistinguishable from a spectacular strategy.

Scope, deliberately narrow
--------------------------
Only instruments **quoted in USD** are supported, because for those the P&L is
already in account currency:

    profit_usd = (exit - entry) * lots * contract_size

A JPY-quoted pair (USDJPY, GBPJPY) earns JPY, which must be converted at the
USDJPY rate *at the time of each trade* to be stated in dollars. That needs a
second price series, so rather than approximate it, :func:`instrument_for`
raises. An unsupported symbol is a refusal, never a wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Instrument", "INSTRUMENTS", "instrument_for", "is_supported"]


@dataclass(frozen=True)
class Instrument:
    """Contract conventions for one symbol, quoted in USD."""

    symbol: str
    pip: float
    contract_size: float
    note: str = ""

    @property
    def pip_value_per_lot(self) -> float:
        """USD earned per pip per 1.0 lot. Exact for USD-quoted symbols."""
        return self.pip * self.contract_size


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

# Symbols we can load but must not price, with the reason.
UNSUPPORTED: dict[str, str] = {
    "USDJPY": "quoted in JPY; P&L needs USDJPY conversion per trade",
    "GBPJPY": "quoted in JPY; P&L needs USDJPY conversion per trade",
    "EURJPY": "quoted in JPY; P&L needs USDJPY conversion per trade",
    "AUDJPY": "quoted in JPY; P&L needs USDJPY conversion per trade",
    "CHFJPY": "quoted in JPY; P&L needs USDJPY conversion per trade",
    "USDCHF": "quoted in CHF; P&L needs USDCHF conversion per trade",
    "USDCAD": "quoted in CAD; P&L needs USDCAD conversion per trade",
}


def is_supported(symbol: str) -> bool:
    return str(symbol).upper() in INSTRUMENTS


def instrument_for(symbol: str) -> Instrument:
    """Look up conventions, refusing symbols we cannot price correctly."""
    key = str(symbol).upper()
    if key in INSTRUMENTS:
        return INSTRUMENTS[key]
    if key in UNSUPPORTED:
        raise ValueError(
            f"{key} is not priceable here: {UNSUPPORTED[key]}. "
            "Load the conversion series and extend instruments.py rather than "
            "assuming FX-major conventions.")
    raise ValueError(
        f"unknown instrument {key}; add it to instruments.py with its real "
        "pip and contract size. Guessing produces P&L that is wrong by a "
        "factor of 100 without failing.")
