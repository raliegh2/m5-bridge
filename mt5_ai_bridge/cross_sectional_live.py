"""Turn a cross-sectional rebalance into orders the live risk engine allows.

:mod:`cross_sectional` decides *what* the book should hold. It says nothing
about size, and on a real account that is most of the problem: the research
replay assumes every name can be held at an equal weight, while the account has
$4,802.43, a one-share minimum, and a risk engine that refuses to size a signal
whose measured edge is zero.

This module is the join. Each leg goes through the same
:class:`~mt5_ai_bridge.risk_v18.RiskEngine` the live system uses, so a
cross-sectional book is subject to exactly the limits everything else is:

* fractional-Kelly sizing off the signal's *measured* edge -- no edge, no
  position, which is the honest outcome for an unproven ranking;
* the drawdown governor and kill switch;
* per-symbol and aggregate risk, and margin caps;
* whole shares, refusing any leg whose single share exceeds its budget.

Two structural mismatches are surfaced rather than smoothed over, because both
are real and neither should be discovered live:

**Position count.** ``RiskBudget.max_concurrent_positions`` defaults to 5. A
twenty-a-side book asks for forty. The plan reports how many legs the cap
admits instead of quietly truncating, because a 40-name market-neutral book
truncated to 5 is not the strategy -- it is five arbitrary bets.

**Factor budget.** Every US equity loads the same factor (see
:func:`~mt5_ai_bridge.risk_v18.exposure_groups`), so the 4% correlated-exposure
cap is consumed by the whole book at once. That cap exists for a *directional*
book. A market-neutral one nets to roughly zero factor exposure, so the plan
reports gross and net separately and lets the caller judge; it does not assume
the neutrality it has not verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .enums import Signal
from .risk_v18 import RiskEngine, exposure_groups

__all__ = ["RebalanceLeg", "PlannedLeg", "RebalancePlan", "plan_rebalance"]

EQUITY_PIP = 0.01          # one cent
EQUITY_CONTRACT = 1.0      # one lot is one share
MIN_SHARES = 1.0


@dataclass(frozen=True)
class RebalanceLeg:
    """One name the ranking book wants to hold this period."""

    symbol: str
    side: Signal
    price: float
    stop_distance: float

    def validate(self) -> None:
        if self.price <= 0:
            raise ValueError(f"{self.symbol}: price must be positive")
        if self.stop_distance <= 0:
            raise ValueError(f"{self.symbol}: stop distance must be positive")
        if self.side not in (Signal.BUY, Signal.SELL):
            raise ValueError(f"{self.symbol}: side must be BUY or SELL")


@dataclass(frozen=True)
class PlannedLeg:
    symbol: str
    side: Signal
    shares: int
    risk_fraction: float
    notional: float
    reason: str


@dataclass
class RebalancePlan:
    accepted: List[PlannedLeg] = field(default_factory=list)
    rejected: List[PlannedLeg] = field(default_factory=list)
    balance: float = 0.0

    @property
    def requested(self) -> int:
        return len(self.accepted) + len(self.rejected)

    @property
    def gross_risk_fraction(self) -> float:
        return round(sum(leg.risk_fraction for leg in self.accepted), 6)

    @property
    def gross_notional(self) -> float:
        return round(sum(leg.notional for leg in self.accepted), 2)

    @property
    def net_notional(self) -> float:
        """Long notional minus short notional: the residual directional bet."""
        signed = sum(leg.notional if leg.side is Signal.BUY else -leg.notional
                     for leg in self.accepted)
        return round(signed, 2)

    @property
    def is_balanced(self) -> bool:
        """Whether the admitted book is still market-neutral in notional.

        A neutral strategy whose short legs were rejected is a long book, and
        that is exactly how a hedged strategy turns into a directional one
        without anyone deciding to.
        """
        if not self.accepted or self.gross_notional <= 0:
            return False
        return abs(self.net_notional) / self.gross_notional <= 0.10

    def rejection_reasons(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for leg in self.rejected:
            key = leg.reason.split(":")[0].strip()
            out[key] = out.get(key, 0) + 1
        return out

    def summary(self) -> dict:
        longs = [leg for leg in self.accepted if leg.side is Signal.BUY]
        shorts = [leg for leg in self.accepted if leg.side is Signal.SELL]
        return {
            "requested": self.requested,
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "longs": len(longs), "shorts": len(shorts),
            "gross_risk_fraction": self.gross_risk_fraction,
            "gross_notional": self.gross_notional,
            "net_notional": self.net_notional,
            "market_neutral": self.is_balanced,
            "rejection_reasons": self.rejection_reasons(),
        }


def plan_rebalance(legs: Sequence[RebalanceLeg], *, balance: float,
                   equity: float, engine: RiskEngine, edge: dict,
                   leverage: float = 100.0,
                   open_risk: Optional[Dict[str, float]] = None,
                   open_margin: Optional[Dict[str, float]] = None
                   ) -> RebalancePlan:
    """Size every leg through the live risk engine, in order.

    Legs are offered in the order given and each accepted one consumes budget,
    so the caller controls priority. The engine's refusals are recorded with
    their reasons rather than retried at a smaller size: a leg the risk system
    declined is not a smaller trade, it is not a trade.
    """
    plan = RebalancePlan(balance=balance)
    running_risk = dict(open_risk or {})
    running_margin = dict(open_margin or {})
    running_sides: Dict[str, int] = {}

    for leg in legs:
        leg.validate()
        side = 1 if leg.side is Signal.BUY else -1
        decision = engine.size(
            symbol=leg.symbol, balance=balance, equity=equity,
            stop_distance=leg.stop_distance, pip=EQUITY_PIP,
            pip_value_per_lot=EQUITY_PIP * EQUITY_CONTRACT, edge=edge,
            open_risk=running_risk, min_lot=MIN_SHARES, max_lot=1e9,
            lot_step=MIN_SHARES, price=leg.price,
            contract_size=EQUITY_CONTRACT, leverage=leverage,
            open_margin=running_margin, sides=running_sides, side=side)

        shares = int(decision.lots)
        notional = shares * leg.price
        planned = PlannedLeg(symbol=leg.symbol, side=leg.side, shares=shares,
                             risk_fraction=round(decision.risk_fraction, 6),
                             notional=round(notional, 2),
                             reason=decision.reason)
        if not decision.approved or shares < MIN_SHARES:
            plan.rejected.append(planned)
            continue

        plan.accepted.append(planned)
        running_risk[leg.symbol] = decision.risk_fraction
        running_sides[leg.symbol] = side
        if equity > 0:
            running_margin[leg.symbol] = notional / leverage / equity

    return plan


def factor_exposure(plan: RebalancePlan) -> Dict[str, float]:
    """Signed notional per risk factor for an accepted book."""
    out: Dict[str, float] = {}
    for leg in plan.accepted:
        signed = leg.notional if leg.side is Signal.BUY else -leg.notional
        for group in exposure_groups(leg.symbol):
            out[group] = round(out.get(group, 0.0) + signed, 2)
    return out
