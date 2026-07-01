"""Pre-trade risk checks and a realised daily-loss tracker.

Limits are injected (from Settings) rather than module-level constants, so the
engine is configurable and testable.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class RiskLimits:
    daily_max_loss: float = 250.0
    total_max_loss: float = 500.0
    max_open_positions: int = 3


@dataclass(frozen=True)
class RiskResult:
    ok: bool
    message: str

    def __iter__(self):  # backwards compatible: ok, message = check_risk(...)
        yield self.ok
        yield self.message


class DailyLossTracker:
    """Tracks drawdown from the day's starting equity.

    The first equity observed on a given (UTC) date is taken as that day's
    baseline; ``update`` returns ``start_equity - equity`` so far. Because
    equity = balance + floating P&L, this captures both realised losses from
    closed trades and open-position drawdown — a truer daily stop than the
    floating-only check.
    """

    def __init__(self) -> None:
        self._day = None
        self._start_equity: Optional[float] = None

    def update(self, equity: float, today=None) -> float:
        today = today or datetime.now(timezone.utc).date()
        if today != self._day:
            self._day = today
            self._start_equity = equity
        return self._start_equity - equity

    @property
    def day(self):
        return self._day

    @property
    def start_equity(self) -> Optional[float]:
        return self._start_equity


def check_risk(account, positions, limits: Optional[RiskLimits] = None,
               daily_loss: Optional[float] = None) -> RiskResult:
    """Validate account state against risk limits.

    ``account`` must expose ``balance`` and ``equity``; ``positions`` is a
    sequence (or None). If ``daily_loss`` is supplied (from DailyLossTracker)
    it is used for the daily limit; otherwise the floating loss is used, which
    preserves the original behaviour. Returns a RiskResult that also unpacks to
    (ok, message).
    """
    limits = limits or RiskLimits()

    floating_loss = account.balance - account.equity
    open_positions = len(positions) if positions else 0
    effective_daily = daily_loss if daily_loss is not None else floating_loss

    if floating_loss >= limits.total_max_loss:
        return RiskResult(False, "Total loss limit reached.")

    if effective_daily >= limits.daily_max_loss:
        return RiskResult(False, "Daily loss limit reached.")

    if open_positions >= limits.max_open_positions:
        return RiskResult(False, "Maximum open positions reached.")

    return RiskResult(True, "Risk check passed.")
