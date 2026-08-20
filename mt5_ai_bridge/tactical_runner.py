"""Run the tactical book live: monthly, long-or-flat, whole shares.

The research lives in :mod:`mt5_ai_bridge.tactical_allocation`. This is the
part that talks to a broker: read completed daily bars, compare each leg to its
ten-month average, work out how many whole shares that implies, and place the
difference.

Design notes worth stating, because each is a decision rather than a detail:

**Completed bars only.** The decision reads bars from ``start=1``, so today's
forming candle never informs a trade. A rule that peeks at an unfinished bar
backtests beautifully and cannot be traded.

**Monthly, not continuously.** The rule is defined on month boundaries.
:func:`is_rebalance_due` gates on the calendar month actually changing, so
restarting the bot ten times in a day does not produce ten rebalances.

**Risk integration is partial on purpose.** ``RiskEngine.size`` derives lots
from a stop distance and a measured Kelly edge. This book has no stop -- it is
long or flat on a monthly signal -- so Kelly sizing does not apply and forcing
it would be theatre. What does apply is carried over in full: the
:class:`~mt5_ai_bridge.risk_v18.DrawdownGovernor` scales exposure down as
equity falls from its peak, and the :class:`~mt5_ai_bridge.risk_v18.KillSwitch`
flattens the book outright. Position size otherwise comes from the strategy's
own weights, which is what defines it.

**Shares, not lots.** One ETF lot is one share and shares are indivisible, so
targets floor rather than round: a target of 26.9 shares is 26.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from .enums import Mode
from .logging_config import get_logger
from .risk_v18 import DrawdownGovernor, KillSwitch
from .tactical_allocation import LOCKED_TACTICAL, TacticalConfig
from .trade_manager import close_position

log = get_logger("tactical")

__all__ = ["TacticalLeg", "LegPlan", "month_key", "is_rebalance_due",
           "leg_signal", "target_shares", "held_shares", "plan_rebalance",
           "apply_plans", "TACTICAL_MAGIC"]

TACTICAL_MAGIC = 20260801


@dataclass(frozen=True)
class TacticalLeg:
    """One sleeve of the book: a symbol and its share of invested capital."""

    symbol: str
    weight: float

    def validate(self) -> None:
        if not self.symbol:
            raise ValueError("leg needs a symbol")
        if not 0.0 < self.weight <= 1.0:
            raise ValueError(f"{self.symbol}: weight must be in (0, 1]")


@dataclass(frozen=True)
class LegPlan:
    symbol: str
    above_average: bool
    price: float
    target_shares: int
    current_shares: int
    reason: str

    @property
    def delta(self) -> int:
        return self.target_shares - self.current_shares

    @property
    def action(self) -> str:
        if self.delta > 0:
            return "BUY"
        if self.delta < 0:
            return "SELL"
        return "HOLD"

    def describe(self) -> str:
        return (f"{self.action} {abs(self.delta)} {self.symbol} "
                f"@ {self.price:.2f} (hold {self.target_shares}, "
                f"have {self.current_shares}) -- {self.reason}")


def month_key(when: datetime) -> Tuple[int, int]:
    return (when.year, when.month)


def is_rebalance_due(now: datetime, last: Optional[datetime]) -> bool:
    """True when the calendar month has turned since the last rebalance."""
    if last is None:
        return True
    return month_key(now) != month_key(last)


def leg_signal(client, symbol: str, cfg: TacticalConfig = LOCKED_TACTICAL
               ) -> Optional[Tuple[bool, float, float]]:
    """(above_average, last completed close, average) for ``symbol``.

    Returns None when the broker cannot supply enough completed history to
    form the average -- an unknown signal must not be read as "sell".
    """
    need = cfg.sma_days
    client.symbol_select(symbol, True)
    # start=1 skips the bar still forming, so nothing peeks at the future.
    rates = client.copy_rates_from_pos(symbol, "D1", 1, need)
    if rates is None or len(rates) < need:
        log.warning("%s: only %s completed daily bars, need %s", symbol,
                    0 if rates is None else len(rates), need)
        return None

    closes = [float(bar["close"]) for bar in rates[-need:]]
    average = sum(closes) / len(closes)
    last = closes[-1]
    return bool(last > average), last, average


def target_shares(balance: float, weight: float, fraction_invested: float,
                  price: float) -> int:
    """Whole shares implied by a weight, flooring so exposure is never over."""
    if price <= 0 or balance <= 0 or fraction_invested <= 0:
        return 0
    return int((balance * fraction_invested * weight) // price)


def held_shares(client, symbol: str, magic: int = TACTICAL_MAGIC) -> int:
    """Shares currently held for ``symbol`` by this strategy only.

    Filtering on magic keeps the book from adopting or closing positions that
    another engine on the same account opened.
    """
    positions = client.positions_get(symbol=symbol) or []
    total = 0.0
    for position in positions:
        if str(getattr(position, "symbol", "")).upper() != symbol.upper():
            continue
        if int(getattr(position, "magic", 0) or 0) != magic:
            continue
        total += float(getattr(position, "volume", 0.0) or 0.0)
    return int(total)


def plan_rebalance(client, legs: Sequence[TacticalLeg],
                   cfg: TacticalConfig = LOCKED_TACTICAL,
                   fraction_invested: float = 0.70,
                   magic: int = TACTICAL_MAGIC,
                   governor: Optional[DrawdownGovernor] = None,
                   kill_switch: Optional[KillSwitch] = None
                   ) -> List[LegPlan]:
    """Decide the whole book. Reads the account and the market; sends nothing."""
    for leg in legs:
        leg.validate()
    total_weight = sum(leg.weight for leg in legs)
    if total_weight > 1.0 + 1e-9:
        raise ValueError(f"leg weights sum to {total_weight:.3f}, over 1.0")

    account = client.account_info()
    if account is None:
        log.warning("no account info; standing down")
        return []
    balance = float(getattr(account, "balance", 0.0) or 0.0)
    equity = float(getattr(account, "equity", balance) or balance)

    effective = fraction_invested
    halt_reason = ""
    if governor is not None:
        governor.observe(equity)
        multiplier = governor.multiplier(equity)
        if multiplier < 1.0:
            log.info("drawdown governor scaling exposure to %.0f%%",
                     multiplier * 100)
        effective *= multiplier
    if kill_switch is not None:
        peak = governor.peak_equity if governor is not None else equity
        allowed, why = kill_switch.check(equity, peak)
        if not allowed:
            effective = 0.0
            halt_reason = f"kill switch: {why}"
            log.warning("%s -- flattening the book", halt_reason)

    plans: List[LegPlan] = []
    for leg in legs:
        signal = leg_signal(client, leg.symbol, cfg)
        if signal is None:
            continue
        above, close, average = signal
        if halt_reason:
            target, reason = 0, halt_reason
        elif above:
            target = target_shares(balance, leg.weight, effective, close)
            reason = f"{close:.2f} above {cfg.sma_months}m average {average:.2f}"
        else:
            target, reason = 0, (f"{close:.2f} below {cfg.sma_months}m average "
                                 f"{average:.2f}")
        plans.append(LegPlan(symbol=leg.symbol, above_average=above,
                             price=close, target_shares=target,
                             current_shares=held_shares(client, leg.symbol,
                                                        magic),
                             reason=reason))
    return plans


def apply_plans(client, journal, settings, plans: Sequence[LegPlan],
                magic: int = TACTICAL_MAGIC) -> List[Tuple[bool, str]]:
    """Place the difference between target and held, honouring the mode.

    Exits are processed before entries so capital freed by a sale is available
    to the purchase in the same rebalance.
    """
    from .execution import place_market_order       # local: avoids a cycle

    results: List[Tuple[bool, str]] = []
    ordered = sorted(plans, key=lambda p: p.delta)   # sells first
    for plan in ordered:
        if plan.delta == 0:
            journal.log_signal(plan.symbol, "HOLD", plan.reason, 0.0)
            continue

        if settings.mode is Mode.READ_ONLY:
            log.info("READ_ONLY: would %s", plan.describe())
            journal.log_signal(plan.symbol, plan.action,
                               f"READ_ONLY: {plan.reason}", 0.0)
            results.append((False, f"READ_ONLY: {plan.describe()}"))
            continue

        if settings.mode is Mode.APPROVAL:
            answer = input(f"Place demo {plan.describe()} ? Type YES: ")
            if answer != "YES":
                log.info("Skipped by user: %s", plan.symbol)
                results.append((False, "skipped by user"))
                continue

        if plan.delta < 0:
            ok, message = _reduce(client, plan, magic)
        else:
            ok, message = place_market_order(
                client, plan.symbol, "BUY", float(plan.delta),
                magic=magic, comment="TACTICAL")
        log.info("%s: %s", plan.symbol, message)
        journal.log_order(plan.symbol, plan.action, float(abs(plan.delta)),
                          plan.price, None, None, None,
                          "TACTICAL_REBALANCE", message)
        results.append((ok, message))
    return results


def _reduce(client, plan: LegPlan, magic: int) -> Tuple[bool, str]:
    """Close positions for a leg until the held size meets the target.

    Closes whole positions, newest first, and stops as soon as the remaining
    size is at or below target -- so a partial reduction never overshoots into
    an accidental short.
    """
    positions = [p for p in (client.positions_get(symbol=plan.symbol) or [])
                 if str(getattr(p, "symbol", "")).upper() == plan.symbol.upper()
                 and int(getattr(p, "magic", 0) or 0) == magic]
    positions.sort(key=lambda p: int(getattr(p, "ticket", 0)), reverse=True)

    remaining = plan.current_shares
    messages = []
    for position in positions:
        if remaining <= plan.target_shares:
            break
        ok, message = close_position(client, int(getattr(position, "ticket", 0)))
        messages.append(message)
        if not ok:
            return False, "; ".join(messages)
        remaining -= int(float(getattr(position, "volume", 0.0) or 0.0))
    return True, "; ".join(messages) or "nothing to close"
