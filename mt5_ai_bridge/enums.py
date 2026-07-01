"""Domain enums shared across the package.

Using these instead of raw strings / MetaTrader5 integer constants keeps the
trading logic decoupled from the broker library so it can be unit-tested
without MetaTrader5 installed.
"""

from enum import Enum


class Mode(str, Enum):
    """Operating mode for the main loop."""

    READ_ONLY = "READ_ONLY"  # observe + journal only, never trade
    APPROVAL = "APPROVAL"    # require manual confirmation before each action
    AUTO = "AUTO"            # place trades automatically when risk allows

    @classmethod
    def from_str(cls, value: str) -> "Mode":
        try:
            return cls(str(value).upper())
        except ValueError:
            return cls.APPROVAL


class Signal(str, Enum):
    """Strategy output."""

    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

    @property
    def is_trade(self) -> bool:
        return self in (Signal.BUY, Signal.SELL)


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
