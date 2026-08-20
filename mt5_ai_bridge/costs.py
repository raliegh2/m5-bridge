"""Realistic transaction costs for backtests.

Every historical replay in this repo priced fills at the exact mid close and the
exact stop/target level, so a reported edge was really a *gross* edge. On the
fast books that is not a rounding error: ``research/v14_4_cost_stress_report.py``
shows a 5-pip-stop scalp at 1.25R loses its entire expectancy to roughly 0.4
pips of round-trip cost. Costs therefore belong in the engine, not in a
footnote.

Model
-----
Bars are assumed to carry **mid** prices (the MT5 exporter writes bid, which for
these purposes differs by well under the spreads modelled here; set
``spread_pips`` to the *effective* round-trip spread you actually pay and the
distinction disappears).

A trade crosses the spread twice, and is slipped against on both sides:

* a BUY  enters at ``mid + half_spread + slippage`` and exits at
  ``mid - half_spread - slippage``
* a SELL enters at ``mid - half_spread - slippage`` and exits at
  ``mid + half_spread + slippage``

Commission is charged per lot per round turn, and swap per lot per night held.
All four components are independent so a broker's actual schedule can be
expressed exactly.

The functions are pure and unit-tested; nothing here reads configuration or
touches the broker.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import OrderSide, Signal

__all__ = [
    "CostModel",
    "ZERO_COST",
    "RETAIL_TIGHT",
    "RETAIL_TYPICAL",
    "RETAIL_WIDE",
    "PRESETS",
    "preset",
    "breakeven_win_rate",
    "cost_adjusted_expectancy_r",
]


def _is_buy(side) -> bool:
    """True for a long side, accepting Signal, OrderSide or a plain string."""
    if isinstance(side, (Signal, OrderSide)):
        return side.value == "BUY"
    text = str(side).upper()
    if text.endswith(".BUY") or text == "BUY":
        return True
    if text.endswith(".SELL") or text == "SELL":
        return False
    raise ValueError(f"cannot interpret {side!r} as a trade side")


@dataclass(frozen=True)
class CostModel:
    """Broker costs in pips and dollars.

    ``spread_pips`` is the full quoted spread (both sides combined), so a single
    round trip pays it once in total: half on entry, half on exit.
    ``slippage_pips`` is charged *per side*, always against the trader.
    """

    spread_pips: float = 0.0
    slippage_pips: float = 0.0
    commission_per_lot_round_turn: float = 0.0
    swap_pips_per_night_long: float = 0.0
    swap_pips_per_night_short: float = 0.0

    def __post_init__(self) -> None:
        for name in ("spread_pips", "slippage_pips",
                     "commission_per_lot_round_turn"):
            if float(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")

    # --- price adjustments -------------------------------------------------

    @property
    def half_spread_pips(self) -> float:
        return self.spread_pips / 2.0

    @property
    def per_side_pips(self) -> float:
        """Price concession suffered on each side of the trade."""
        return self.half_spread_pips + self.slippage_pips

    @property
    def round_trip_pips(self) -> float:
        """Total price-based cost of opening and closing, in pips."""
        return self.spread_pips + 2.0 * self.slippage_pips

    def entry_price(self, side, mid_price: float, pip: float) -> float:
        """Fill price when opening ``side`` at ``mid_price``."""
        edge = self.per_side_pips * pip
        return mid_price + edge if _is_buy(side) else mid_price - edge

    def exit_price(self, side, mid_price: float, pip: float) -> float:
        """Fill price when closing a ``side`` position at ``mid_price``."""
        edge = self.per_side_pips * pip
        return mid_price - edge if _is_buy(side) else mid_price + edge

    # --- money costs -------------------------------------------------------

    def commission_cost(self, lots: float) -> float:
        """Dollar commission for a full round turn of ``lots``."""
        return self.commission_per_lot_round_turn * max(lots, 0.0)

    def swap_pips(self, side, nights: int) -> float:
        """Swap in pips for holding ``side`` over ``nights`` (negative = cost)."""
        if nights <= 0:
            return 0.0
        rate = (self.swap_pips_per_night_long if _is_buy(side)
                else self.swap_pips_per_night_short)
        return rate * nights

    def swap_cost(self, side, lots: float, nights: int,
                  pip_value_per_lot: float = 10.0) -> float:
        """Dollar swap cost (positive = money lost) for the held nights."""
        return -self.swap_pips(side, nights) * lots * pip_value_per_lot

    def total_cost(self, side, lots: float, nights: int = 0,
                   pip_value_per_lot: float = 10.0) -> float:
        """All-in dollar cost of a round turn, excluding price direction.

        The spread/slippage portion is expressed in money here so it can be
        reported alongside commission and swap; a replay that already applies
        :meth:`entry_price` and :meth:`exit_price` must NOT add it again.
        """
        price_cost = self.round_trip_pips * lots * pip_value_per_lot
        return (price_cost
                + self.commission_cost(lots)
                + self.swap_cost(side, lots, nights, pip_value_per_lot))

    def commission_pips(self, lots: float, pip_value_per_lot: float = 10.0) -> float:
        """Commission re-expressed in pips, for R-multiple reasoning."""
        if lots <= 0 or pip_value_per_lot <= 0:
            return 0.0
        return self.commission_cost(lots) / (lots * pip_value_per_lot)

    def all_in_pips(self, lots: float = 0.01,
                    pip_value_per_lot: float = 10.0, nights: int = 0,
                    side=Signal.BUY) -> float:
        """Round-trip cost in pips including commission and swap."""
        return (self.round_trip_pips
                + self.commission_pips(lots, pip_value_per_lot)
                - self.swap_pips(side, nights))


# Zero cost reproduces the historical (gross) replays exactly.
ZERO_COST = CostModel()

# Indicative retail FX majors. Override per broker/symbol -- these exist so a
# replay cannot silently default to "free".
RETAIL_TIGHT = CostModel(spread_pips=0.4, slippage_pips=0.1,
                         commission_per_lot_round_turn=7.0,
                         swap_pips_per_night_long=-0.3,
                         swap_pips_per_night_short=-0.1)
RETAIL_TYPICAL = CostModel(spread_pips=0.9, slippage_pips=0.2,
                           commission_per_lot_round_turn=0.0,
                           swap_pips_per_night_long=-0.5,
                           swap_pips_per_night_short=-0.2)
RETAIL_WIDE = CostModel(spread_pips=1.8, slippage_pips=0.4,
                        commission_per_lot_round_turn=0.0,
                        swap_pips_per_night_long=-0.9,
                        swap_pips_per_night_short=-0.4)

PRESETS = {
    "zero": ZERO_COST,
    "tight": RETAIL_TIGHT,
    "typical": RETAIL_TYPICAL,
    "wide": RETAIL_WIDE,
}


def preset(name: str) -> CostModel:
    """Look up a named preset, raising a helpful error for typos."""
    try:
        return PRESETS[str(name).lower()]
    except KeyError:
        raise ValueError(
            f"unknown cost preset {name!r}; choose from {sorted(PRESETS)}"
        ) from None


# --- analytic helpers ------------------------------------------------------


def breakeven_win_rate(stop_pips: float, target_r: float,
                       cost: CostModel) -> float:
    """Win rate needed to break even once ``cost`` is paid.

    A win nets ``target_r * stop - round_trip`` pips; a loss costs
    ``stop + round_trip`` pips.
    """
    if stop_pips <= 0:
        raise ValueError("stop_pips must be positive")
    win = target_r * stop_pips - cost.round_trip_pips
    loss = stop_pips + cost.round_trip_pips
    if win <= 0:
        return 1.0          # target unreachable after costs
    return loss / (loss + win)


def cost_adjusted_expectancy_r(stop_pips: float, target_r: float,
                               win_rate: float, cost: CostModel) -> float:
    """Expectancy per trade in R units of the *gross* stop, after costs."""
    if stop_pips <= 0:
        raise ValueError("stop_pips must be positive")
    win = target_r * stop_pips - cost.round_trip_pips
    loss = stop_pips + cost.round_trip_pips
    return (win_rate * win - (1.0 - win_rate) * loss) / stop_pips
