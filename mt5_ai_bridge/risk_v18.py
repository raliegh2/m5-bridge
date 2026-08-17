"""Risk management that is correct whether or not an edge exists.

Position sizing is usually treated as a knob -- "risk 1% per trade" -- chosen
before anyone knows whether the strategy makes money. That gets the causality
backwards. The right size is a function of the edge, and when the edge is zero
the right size is zero. This module makes that mechanical rather than a matter
of judgement.

Five layers, applied in order, each able only to *reduce* size:

1. :func:`kelly_fraction` -- the growth-optimal fraction implied by the
   measured win rate and payoff. Negative edge yields a negative fraction,
   which clamps to zero: the system cannot be talked into sizing a losing
   signal.
2. :func:`fractional_kelly` -- a fraction of that, because full Kelly assumes
   the edge is known exactly and it never is. Quarter Kelly is the default and
   the literature's usual compromise.
3. :func:`volatility_target_lots` -- converts a risk budget into lots via the
   instrument's own stop distance, so risk per trade is constant across
   symbols and regimes.
4. :class:`DrawdownGovernor` -- scales exposure down as equity falls from its
   peak, so a losing run shrinks the bet instead of compounding it.
5. :class:`KillSwitch` -- hard limits that stop trading outright.

The layers are pure functions and small state machines, unit-tested without a
broker. Nothing here reads configuration or places orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

__all__ = [
    "RiskProfile",
    "CONSERVATIVE_10PCT",
    "BALANCED_20PCT",
    "PROFILES",
    "risk_profile",
    "kelly_fraction",
    "fractional_kelly",
    "edge_from_trades",
    "volatility_target_lots",
    "DrawdownGovernor",
    "KillSwitch",
    "KillSwitchState",
    "RiskBudget",
    "RiskDecision",
    "RiskEngine",
]


# --- edge measurement and Kelly --------------------------------------------


def kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
    """Growth-optimal fraction of capital to risk.

    ``f* = (p*b - q) / b`` for win probability ``p``, loss probability ``q``
    and payoff ratio ``b`` (average win / average loss).

    Returns 0.0 for a non-positive edge. A negative Kelly fraction is the
    mathematics telling you to take the other side, not to size smaller, and
    silently flipping direction is never what the caller meant.
    """
    if not 0.0 <= win_rate <= 1.0:
        raise ValueError("win_rate must be in [0, 1]")
    if win_loss_ratio <= 0:
        raise ValueError("win_loss_ratio must be positive")
    f = (win_rate * win_loss_ratio - (1.0 - win_rate)) / win_loss_ratio
    return max(0.0, f)


def fractional_kelly(full_kelly: float, fraction: float = 0.25,
                     cap: float = 0.02) -> float:
    """A conservative share of the Kelly fraction, hard-capped.

    Full Kelly assumes the edge is known exactly. It never is: it is estimated
    from a finite sample, and overestimating it is far more damaging than
    underestimating it, because Kelly's growth curve falls away steeply above
    the optimum. Quarter Kelly gives up about 6% of theoretical growth for a
    large reduction in variance and in sensitivity to estimation error.

    ``cap`` is an absolute ceiling on fraction of equity risked per trade,
    applied after the Kelly calculation, because a small sample can produce an
    absurdly high estimate.
    """
    if not 0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    if cap <= 0:
        raise ValueError("cap must be positive")
    return max(0.0, min(full_kelly * fraction, cap))


def edge_from_trades(profits: Sequence[float]) -> dict:
    """Win rate, payoff ratio and Kelly fraction implied by a trade series.

    Returns zeros for a series with no wins or no losses -- an unmeasurable
    edge is reported as no edge, never as an infinite one.
    """
    wins = [p for p in profits if p > 0]
    losses = [-p for p in profits if p < 0]
    n = len(wins) + len(losses)
    if n == 0 or not wins or not losses:
        return {"trades": n, "win_rate": 0.0, "win_loss_ratio": 0.0,
                "kelly": 0.0, "expectancy": 0.0}
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
    expectancy = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)
    return {
        "trades": n,
        "win_rate": round(win_rate, 4),
        "win_loss_ratio": round(ratio, 4),
        "kelly": round(kelly_fraction(win_rate, ratio) if ratio > 0 else 0.0, 4),
        "expectancy": round(expectancy, 4),
    }


# --- sizing ----------------------------------------------------------------


def volatility_target_lots(balance: float, risk_fraction: float,
                           stop_distance: float, pip: float,
                           pip_value_per_lot: float,
                           min_lot: float = 0.01, max_lot: float = 100.0,
                           lot_step: float = 0.01) -> float:
    """Lots such that a stop-out costs exactly ``risk_fraction`` of balance.

    Derives size FROM the stop rather than the other way round, so a volatile
    instrument gets a smaller position and every trade carries the same risk.
    Returns 0.0 when the inputs cannot support a position -- a zero risk
    budget must produce no trade, not a minimum-size one.
    """
    if balance <= 0 or risk_fraction <= 0 or stop_distance <= 0:
        return 0.0
    if pip <= 0 or pip_value_per_lot <= 0:
        return 0.0
    stop_pips = stop_distance / pip
    raw = (balance * risk_fraction) / (stop_pips * pip_value_per_lot)
    if raw < min_lot:
        # Rounding a sub-minimum position UP would silently exceed the risk
        # budget -- the mistake that made gold look profitable earlier.
        return 0.0
    steps = int(raw / lot_step)
    return round(min(steps * lot_step, max_lot), 2)


# --- drawdown governor -----------------------------------------------------


@dataclass
class DrawdownGovernor:
    """Scales exposure down as equity falls from its high-water mark.

    Constant fractional sizing already reduces absolute risk in a drawdown,
    but not fast enough: it takes a 100% gain to recover a 50% loss. Tapering
    exposure as the drawdown deepens trades some recovery speed for a much
    lower probability of ruin.

    Between ``soft_limit`` and ``hard_limit`` the multiplier falls linearly
    from 1.0 to ``floor``; at ``hard_limit`` it is zero and trading stops.
    """

    soft_limit: float = 0.05      # 5% drawdown: begin tapering
    hard_limit: float = 0.20      # 20% drawdown: stop entirely
    floor: float = 0.25           # smallest multiplier before the hard stop
    peak_equity: float = 0.0

    def __post_init__(self) -> None:
        if not 0 < self.soft_limit < self.hard_limit < 1.0:
            raise ValueError("need 0 < soft_limit < hard_limit < 1")
        if not 0 < self.floor <= 1.0:
            raise ValueError("floor must be in (0, 1]")

    def observe(self, equity: float) -> None:
        self.peak_equity = max(self.peak_equity, float(equity))

    def drawdown(self, equity: float) -> float:
        self.observe(equity)
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - equity) / self.peak_equity)

    def multiplier(self, equity: float) -> float:
        dd = self.drawdown(equity)
        if dd <= self.soft_limit:
            return 1.0
        if dd >= self.hard_limit:
            return 0.0
        span = self.hard_limit - self.soft_limit
        progress = (dd - self.soft_limit) / span
        return max(self.floor, 1.0 - progress * (1.0 - self.floor))


# --- kill switches ---------------------------------------------------------


@dataclass
class KillSwitchState:
    tripped: bool = False
    reason: str = ""
    day: str = ""
    day_start_equity: float = 0.0
    realised_today: float = 0.0
    consecutive_losses: int = 0
    trades_today: int = 0


@dataclass
class KillSwitch:
    """Hard limits that stop trading outright rather than sizing down.

    Distinct from the drawdown governor: that shrinks the bet, this refuses
    it. Both exist because a taper alone will still bleed an account to zero
    given a long enough losing run.
    """

    max_daily_loss_fraction: float = 0.02
    max_total_drawdown_fraction: float = 0.20
    max_consecutive_losses: int = 5
    max_trades_per_day: int = 10
    state: KillSwitchState = field(default_factory=KillSwitchState)

    def __post_init__(self) -> None:
        for name in ("max_daily_loss_fraction", "max_total_drawdown_fraction"):
            v = float(getattr(self, name))
            if not 0 < v < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if self.max_consecutive_losses < 1:
            raise ValueError("max_consecutive_losses must be at least 1")
        if self.max_trades_per_day < 1:
            raise ValueError("max_trades_per_day must be at least 1")

    def start_day(self, day: str, equity: float) -> None:
        if self.state.day != day:
            self.state.day = day
            self.state.day_start_equity = float(equity)
            self.state.realised_today = 0.0
            self.state.trades_today = 0
            # A new day clears a daily-loss trip but never a total-drawdown one.
            if self.state.reason.startswith("daily"):
                self.state.tripped = False
                self.state.reason = ""

    def record_trade(self, profit: float) -> None:
        self.state.realised_today += float(profit)
        self.state.trades_today += 1
        if profit < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

    def check(self, equity: float, peak_equity: float) -> tuple[bool, str]:
        """(allowed, reason). Trips latch until the relevant reset."""
        s = self.state
        if s.tripped:
            return False, s.reason

        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity
            if dd >= self.max_total_drawdown_fraction:
                s.tripped = True
                s.reason = (f"total drawdown {dd:.1%} reached the "
                            f"{self.max_total_drawdown_fraction:.0%} limit")
                return False, s.reason

        if s.day_start_equity > 0:
            loss = (s.day_start_equity - equity) / s.day_start_equity
            if loss >= self.max_daily_loss_fraction:
                s.tripped = True
                s.reason = (f"daily loss {loss:.1%} reached the "
                            f"{self.max_daily_loss_fraction:.0%} limit")
                return False, s.reason

        if s.consecutive_losses >= self.max_consecutive_losses:
            return False, (f"{s.consecutive_losses} consecutive losses "
                           f"(limit {self.max_consecutive_losses})")
        if s.trades_today >= self.max_trades_per_day:
            return False, (f"{s.trades_today} trades today "
                           f"(limit {self.max_trades_per_day})")
        return True, "within all limits"


# --- budgets ---------------------------------------------------------------


@dataclass
class RiskBudget:
    """Aggregate exposure caps across concurrent positions."""

    max_total_risk_fraction: float = 0.06
    max_currency_risk_fraction: float = 0.04
    max_symbol_risk_fraction: float = 0.02
    max_concurrent_positions: int = 5

    def __post_init__(self) -> None:
        if self.max_symbol_risk_fraction > self.max_total_risk_fraction:
            raise ValueError("per-symbol risk cannot exceed total risk")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be at least 1")

    def room_for(self, symbol: str, requested: float,
                 open_risk: Dict[str, float]) -> tuple[float, str]:
        """Largest permissible risk fraction for ``symbol``, and why.

        ``open_risk`` maps symbol -> risk fraction currently committed.
        """
        if len(open_risk) >= self.max_concurrent_positions:
            return 0.0, (f"{len(open_risk)} positions open "
                         f"(limit {self.max_concurrent_positions})")

        allowed = min(requested, self.max_symbol_risk_fraction)
        reason = "within budget"
        if allowed < requested:
            reason = f"capped at {self.max_symbol_risk_fraction:.1%} per symbol"

        total = sum(open_risk.values())
        headroom = self.max_total_risk_fraction - total
        if headroom <= 0:
            return 0.0, (f"aggregate risk {total:.1%} at the "
                         f"{self.max_total_risk_fraction:.0%} ceiling")
        if allowed > headroom:
            allowed = headroom
            reason = f"limited by aggregate headroom {headroom:.2%}"

        base, quote = symbol[:3].upper(), symbol[3:6].upper()
        for ccy in (base, quote):
            committed = sum(
                r for s, r in open_risk.items()
                if ccy in (s[:3].upper(), s[3:6].upper()))
            ccy_room = self.max_currency_risk_fraction - committed
            if ccy_room <= 0:
                return 0.0, (f"{ccy} exposure {committed:.1%} at the "
                             f"{self.max_currency_risk_fraction:.0%} cap")
            if allowed > ccy_room:
                allowed = ccy_room
                reason = f"limited by {ccy} headroom {ccy_room:.2%}"

        return max(0.0, allowed), reason


# --- the engine ------------------------------------------------------------


@dataclass
class RiskDecision:
    approved: bool
    lots: float
    risk_fraction: float
    reason: str
    detail: dict = field(default_factory=dict)


@dataclass
class RiskEngine:
    """Composes every layer into a single sizing decision."""

    budget: RiskBudget = field(default_factory=RiskBudget)
    governor: DrawdownGovernor = field(default_factory=DrawdownGovernor)
    kill_switch: KillSwitch = field(default_factory=KillSwitch)
    kelly_fraction_used: float = 0.25
    max_risk_per_trade: float = 0.02

    def size(self, *, symbol: str, balance: float, equity: float,
             stop_distance: float, pip: float, pip_value_per_lot: float,
             edge: dict, open_risk: Optional[Dict[str, float]] = None,
             min_lot: float = 0.01, max_lot: float = 100.0) -> RiskDecision:
        """Decide whether to trade and at what size.

        ``edge`` is the output of :func:`edge_from_trades` for the signal's
        validated track record. A zero or negative Kelly fraction produces a
        zero position: an unproven signal is not sized small, it is not traded.
        """
        open_risk = dict(open_risk or {})
        detail: dict = {}

        allowed, why = self.kill_switch.check(equity, self.governor.peak_equity)
        if not allowed:
            return RiskDecision(False, 0.0, 0.0, f"kill switch: {why}", detail)

        kelly = float(edge.get("kelly", 0.0))
        detail["kelly"] = kelly
        if kelly <= 0:
            return RiskDecision(
                False, 0.0, 0.0,
                "no measured edge: Kelly fraction is zero, so the "
                "growth-optimal position is zero", detail)

        risk = fractional_kelly(kelly, self.kelly_fraction_used,
                                self.max_risk_per_trade)
        detail["fractional_kelly"] = risk

        multiplier = self.governor.multiplier(equity)
        detail["drawdown_multiplier"] = multiplier
        detail["drawdown"] = self.governor.drawdown(equity)
        if multiplier <= 0:
            return RiskDecision(False, 0.0, 0.0,
                                "drawdown governor: hard limit reached", detail)
        risk *= multiplier

        risk, budget_reason = self.budget.room_for(symbol, risk, open_risk)
        detail["budget_reason"] = budget_reason
        if risk <= 0:
            return RiskDecision(False, 0.0, 0.0,
                                f"risk budget: {budget_reason}", detail)

        lots = volatility_target_lots(
            balance, risk, stop_distance, pip, pip_value_per_lot,
            min_lot=min_lot, max_lot=max_lot)
        detail["lots_before_rounding"] = lots
        if lots <= 0:
            return RiskDecision(
                False, 0.0, risk,
                "position below the minimum lot; rounding up would exceed the "
                "risk budget", detail)

        return RiskDecision(True, lots, risk,
                            f"sized from measured edge ({budget_reason})",
                            detail)


# --- named risk profiles ---------------------------------------------------


@dataclass(frozen=True)
class RiskProfile:
    """A complete, coherent risk configuration with a stated ceiling.

    The ceiling is enforced, not hoped for: the kill switch latches at
    ``max_drawdown`` and does not clear on a recovery, and the taper shrinks
    exposure long before it. Reaching the ceiling requires a run of
    maximum-size full-stop losses that the consecutive-loss cutout is sized to
    interrupt first.
    """

    name: str
    max_drawdown: float
    max_risk_per_trade: float
    kelly_fraction_used: float
    budget: RiskBudget
    governor: DrawdownGovernor
    kill_switch_template: dict

    def build(self) -> "RiskEngine":
        """A fresh engine configured to this profile."""
        return RiskEngine(
            budget=RiskBudget(**vars(self.budget)),
            governor=DrawdownGovernor(**{k: v for k, v in
                                         vars(self.governor).items()
                                         if k != "peak_equity"}),
            kill_switch=KillSwitch(**self.kill_switch_template),
            kelly_fraction_used=self.kelly_fraction_used,
            max_risk_per_trade=self.max_risk_per_trade,
        )


# A 10% ceiling. Everything is scaled to make 10% hard to reach rather than
# merely forbidden at the boundary: a third of the per-trade risk, an eighth
# Kelly, tapering from 3%, and a 3-loss cutout.
CONSERVATIVE_10PCT = RiskProfile(
    name="conservative-10pct",
    max_drawdown=0.10,
    max_risk_per_trade=0.0075,
    kelly_fraction_used=0.125,
    budget=RiskBudget(max_total_risk_fraction=0.02,
                      max_currency_risk_fraction=0.015,
                      max_symbol_risk_fraction=0.0075,
                      max_concurrent_positions=3),
    governor=DrawdownGovernor(soft_limit=0.03, hard_limit=0.10, floor=0.20),
    kill_switch_template=dict(max_daily_loss_fraction=0.01,
                              max_total_drawdown_fraction=0.10,
                              max_consecutive_losses=3,
                              max_trades_per_day=6),
)

BALANCED_20PCT = RiskProfile(
    name="balanced-20pct",
    max_drawdown=0.20,
    max_risk_per_trade=0.02,
    kelly_fraction_used=0.25,
    budget=RiskBudget(),
    governor=DrawdownGovernor(),
    kill_switch_template=dict(max_daily_loss_fraction=0.02,
                              max_total_drawdown_fraction=0.20,
                              max_consecutive_losses=5,
                              max_trades_per_day=10),
)

PROFILES = {p.name: p for p in (CONSERVATIVE_10PCT, BALANCED_20PCT)}


def risk_profile(name: str) -> RiskProfile:
    try:
        return PROFILES[str(name).lower()]
    except KeyError:
        raise ValueError(
            f"unknown risk profile {name!r}; choose from {sorted(PROFILES)}"
        ) from None
