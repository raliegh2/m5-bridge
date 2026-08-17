"""Partial profit taking, and what it does and does not do.

Scaling out of a winner and moving the stop to breakeven is the standard way to
reduce drawdown, and it works -- but it is worth being precise about the
mechanism, because it is widely misdescribed.

**What it does.** It truncates the left tail. Once part of the position is
banked and the stop is at entry, the worst case for the remainder is roughly
zero rather than -1R. Across many trades that materially lowers peak-to-trough
drawdown and shortens underwater periods.

**What it does not do.** It does not create expectancy. For a system whose
winners run further than the partial target, scaling out *reduces* gross
expectancy: you cap the part of the distribution that pays for the losses. The
trade is variance for return, and whether that is worth it depends entirely on
which side of the ratio you need to improve.

So this module reports both: expectancy and drawdown, before and after. If
partials improve return/risk they earn their place; if they only shrink both
numbers, they do not.

Simulation walks the actual bar path, so a partial that never fills is not
silently credited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

__all__ = ["PartialPlan", "PartialLeg", "TradeOutcome", "simulate_with_partials",
           "summarise_outcomes"]


@dataclass(frozen=True)
class PartialLeg:
    """Take ``fraction`` of the position off at ``r_multiple``."""

    r_multiple: float
    fraction: float

    def validate(self) -> None:
        if self.r_multiple <= 0:
            raise ValueError("partial target must be a positive R multiple")
        if not 0 < self.fraction < 1:
            raise ValueError("partial fraction must be in (0, 1)")


@dataclass(frozen=True)
class PartialPlan:
    """A scale-out schedule plus the breakeven rule."""

    legs: tuple = ()
    move_stop_to_breakeven_after_leg: Optional[int] = 0
    breakeven_offset_r: float = 0.0

    def validate(self) -> None:
        total = 0.0
        last_r = 0.0
        for leg in self.legs:
            leg.validate()
            if leg.r_multiple <= last_r:
                raise ValueError("partial legs must be in increasing R order")
            last_r = leg.r_multiple
            total += leg.fraction
        if total >= 1.0:
            raise ValueError(
                "partial fractions must sum to less than 1; something has to "
                "remain to reach the final target")
        idx = self.move_stop_to_breakeven_after_leg
        if idx is not None and not 0 <= idx < max(len(self.legs), 1):
            raise ValueError("breakeven leg index is out of range")

    @property
    def enabled(self) -> bool:
        return bool(self.legs)


# A conventional plan: bank half at 1R, stop to breakeven, let the rest run.
HALF_AT_1R = PartialPlan(legs=(PartialLeg(1.0, 0.5),),
                         move_stop_to_breakeven_after_leg=0)


@dataclass
class TradeOutcome:
    """Result of one trade in R units, net of the modelled cost."""

    entry_time: object
    exit_time: object
    side: int
    gross_r: float
    cost_r: float
    legs_filled: int
    reason: str

    @property
    def net_r(self) -> float:
        return self.gross_r - self.cost_r


def simulate_with_partials(bars: pd.DataFrame, signal_index: int, side: int,
                           entry: float, stop: float, target_r: float,
                           max_holding_bars: int,
                           plan: PartialPlan = PartialPlan(),
                           cost_r: float = 0.0) -> TradeOutcome:
    """Walk the bar path applying ``plan``, returning the realised R.

    ``cost_r`` is the round-trip cost expressed in R (cost distance divided by
    the stop distance) and is charged **per filled leg**, because each scale-out
    is a separate fill that crosses the spread. That is the honest treatment and
    it is precisely what makes partials expensive on a tight stop.
    """
    plan.validate()
    risk = (entry - stop) * side
    if not np.isfinite(risk) or risk <= 0:
        return TradeOutcome(None, None, side, 0.0, 0.0, 0, "invalid risk")

    target_price = entry + side * target_r * risk
    remaining = 1.0
    banked_r = 0.0
    legs_filled = 0
    active_stop = stop

    last = min(len(bars) - 1, signal_index + max_holding_bars)
    for i in range(signal_index + 1, last + 1):
        row = bars.iloc[i]
        high, low = float(row["high"]), float(row["low"])

        # Stop first when a bar spans both, matching the rest of the repo.
        stop_hit = low <= active_stop if side > 0 else high >= active_stop
        if stop_hit:
            realised = (active_stop - entry) * side / risk
            banked_r += remaining * realised
            total_cost = cost_r * (legs_filled + 1)
            return TradeOutcome(None, row.get("end", i), side, banked_r,
                                total_cost, legs_filled,
                                "BREAKEVEN" if legs_filled and
                                abs(realised) < 1e-9 else "STOP")

        # Fill any partial legs this bar reached.
        for idx, leg in enumerate(plan.legs):
            if idx < legs_filled:
                continue
            leg_price = entry + side * leg.r_multiple * risk
            reached = high >= leg_price if side > 0 else low <= leg_price
            if not reached:
                break
            banked_r += leg.fraction * leg.r_multiple
            remaining -= leg.fraction
            legs_filled = idx + 1
            if plan.move_stop_to_breakeven_after_leg is not None \
                    and idx >= plan.move_stop_to_breakeven_after_leg:
                active_stop = entry + side * plan.breakeven_offset_r * risk

        target_hit = high >= target_price if side > 0 else low <= target_price
        if target_hit:
            banked_r += remaining * target_r
            total_cost = cost_r * (legs_filled + 1)
            return TradeOutcome(None, row.get("end", i), side, banked_r,
                                total_cost, legs_filled, "TARGET")

    final = bars.iloc[last]
    realised = (float(final["close"]) - entry) * side / risk
    banked_r += remaining * realised
    return TradeOutcome(None, final.get("end", last), side, banked_r,
                        cost_r * (legs_filled + 1), legs_filled, "TIME")


def summarise_outcomes(outcomes: Sequence[TradeOutcome],
                       risk_fraction: float = 0.005,
                       starting_balance: float = 10_000.0) -> dict:
    """Expectancy, profit factor and drawdown for a set of R outcomes.

    Equity is compounded at ``risk_fraction`` of balance per trade, so the
    drawdown figure is the one an account would actually have experienced
    rather than a sum of R multiples.
    """
    if not outcomes:
        return {"trades": 0, "expectancy_r": 0.0, "profit_factor": 0.0,
                "net_r": 0.0, "max_drawdown_pct": 0.0, "final_balance":
                starting_balance}

    rs = np.array([o.net_r for o in outcomes], dtype=float)
    balance = starting_balance
    curve = [balance]
    for r in rs:
        balance += balance * risk_fraction * r
        curve.append(balance)
    eq = np.array(curve)
    peaks = np.maximum.accumulate(eq)
    dd = float(((peaks - eq) / peaks).max()) * 100.0

    wins = rs[rs > 0].sum()
    losses = -rs[rs < 0].sum()
    return {
        "trades": int(rs.size),
        "expectancy_r": round(float(rs.mean()), 5),
        "net_r": round(float(rs.sum()), 3),
        "profit_factor": round(float(wins / losses), 4) if losses > 0 else
        (float("inf") if wins > 0 else 0.0),
        "win_rate": round(float((rs > 0).mean()), 4),
        "max_drawdown_pct": round(dd, 2),
        "final_balance": round(float(eq[-1]), 2),
        "return_pct": round(float((eq[-1] / starting_balance - 1) * 100), 2),
    }
