"""Multi-timeframe trading 'books'.

Books retain timeframe-specific style metadata and distinct magic numbers.
The live dual engine uses the H4 swing book and M15 day book independently;
the remaining book definitions stay available for compatibility/backtests.

- Swing books (H4, D1): trade only when the confirmed higher-timeframe trend is
  aligned (base 1 position each); they stack more on strong momentum.
- Day-trade (M15) and Scalp (M5): NY session only, only on strong momentum
  (base 0), and — when REQUIRE_TREND_ALIGNMENT is on — only in the direction of
  the confirmed trend. This is the key risk technique: never trade against the
  dominant trend.

The confirmed trend is decided by ``trend_bias`` over the confirmation
timeframes (M30 + H4 + D1): every one must agree, or the bot waits. Entries are
read on the fast timeframe (M15) but only fire with that confirmation.

The whole set is bounded by MAX_OPEN_POSITIONS and MAX_TRADES_PER_DAY.
"""

from dataclasses import dataclass
from typing import List, Optional

_BASE_MAGIC = 20260800


@dataclass(frozen=True)
class Book:
    name: str
    timeframe: str
    sl_pips: float
    tp_pips: float
    ny_only: bool
    base_max: int
    strong_max: int
    magic: int


def build_books(settings) -> List[Book]:
    return [
        Book(f"swing-{settings.swing_tf_high}", settings.swing_tf_high,
             settings.swing_sl_pips, settings.swing_tp_pips, False,
             1, settings.swing_strong_max, _BASE_MAGIC + 1),
        Book(f"swing-{settings.swing_tf_higher}", settings.swing_tf_higher,
             settings.swing_sl_pips, settings.swing_tp_pips, False,
             1, settings.swing_strong_max, _BASE_MAGIC + 2),
        Book(f"day-{settings.day_timeframe}", settings.day_timeframe,
             settings.day_sl_pips, settings.day_tp_pips, True,
             1, settings.day_strong_max, _BASE_MAGIC + 3),
        Book(f"scalp-{settings.scalp_timeframe}", settings.scalp_timeframe,
             settings.scalp_sl_pips, settings.scalp_tp_pips, True,
             0, settings.scalp_strong_max, _BASE_MAGIC + 4),
    ]


def desired_positions(book: Book, strong: bool) -> int:
    """How many same-direction positions this book should hold right now."""
    return book.strong_max if strong else book.base_max


def trend_bias(*signals):
    """Confirmed trend direction, or None (=> the bot waits).

    Returns a direction (Signal.BUY / Signal.SELL) ONLY when every supplied
    timeframe signal is that same trade direction. If any timeframe disagrees or
    is WAIT/neutral — or none are given — there is no confirmed trend and this
    returns None, so the bot stays flat.

    Accepts any number of timeframes (e.g. M30 + H4 + D1). The two-argument form
    used previously (H4 + D1) still works unchanged.
    """
    sigs = list(signals)
    if not sigs:
        return None
    first = sigs[0]
    if not first.is_trade:
        return None
    return first if all(s is first for s in sigs) else None
