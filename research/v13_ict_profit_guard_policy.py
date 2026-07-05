"""Research-only ICT guard policy.

This module contains a live-feasible paper-risk guard for the V13 ICT-style
intraday research setup. It is not a broker execution module and does not place
orders. The guard is intended for backtest and supervised simulation only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class ICTResearchGuard:
    """Rolling quality guard for the selected ICT research setup."""

    risk_per_trade_percent: float = 0.35
    daily_stop_r: float = -1.50
    rolling_window_trades: int = 8
    rolling_disable_threshold_r: float = -0.50
    cooldown_months: int = 3
    total_drawdown_stop_percent: float = 8.80
    recent_r: list[float] = field(default_factory=list)
    disabled_until: datetime | None = None

    def is_disabled(self, now: datetime) -> bool:
        return self.disabled_until is not None and now < self.disabled_until

    def should_allow_trade(self, now: datetime, current_day_r: float, current_drawdown_percent: float) -> bool:
        if self.is_disabled(now):
            return False
        if current_day_r <= self.daily_stop_r:
            return False
        if current_drawdown_percent >= self.total_drawdown_stop_percent:
            return False
        return True

    def record_trade_r(self, trade_time: datetime, r_multiple: float) -> None:
        self.recent_r.append(float(r_multiple))
        if len(self.recent_r) > self.rolling_window_trades:
            self.recent_r = self.recent_r[-self.rolling_window_trades :]
        if len(self.recent_r) == self.rolling_window_trades:
            if sum(self.recent_r) <= self.rolling_disable_threshold_r:
                month = trade_time.month + self.cooldown_months
                year = trade_time.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                self.disabled_until = datetime(year, month, 1)
                self.recent_r.clear()


def apply_guard_to_r_series(times: Iterable[datetime], r_values: Iterable[float]) -> list[float]:
    """Return R-multiples accepted by the research guard.

    This helper is intentionally simple and is used for validation/backtesting.
    """

    guard = ICTResearchGuard()
    accepted: list[float] = []
    current_day = None
    current_day_r = 0.0
    equity = 100.0
    peak = 100.0

    for trade_time, r_multiple in zip(times, r_values):
        if current_day != trade_time.date():
            current_day = trade_time.date()
            current_day_r = 0.0
        drawdown = (peak - equity) / peak * 100.0
        if not guard.should_allow_trade(trade_time, current_day_r, drawdown):
            continue
        accepted.append(float(r_multiple))
        current_day_r += float(r_multiple)
        equity += equity * (guard.risk_per_trade_percent / 100.0) * float(r_multiple)
        peak = max(peak, equity)
        guard.record_trade_r(trade_time, float(r_multiple))
    return accepted
