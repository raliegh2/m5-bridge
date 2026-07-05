"""Research-only ICT consistent throttle policy.

This policy is designed for supervised simulation/backtesting. It does not place
broker orders. The goal is to keep an intraday strategy active while reducing
risk during weak rolling-performance periods instead of fully halting the bot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class ICTConsistentThrottlePolicy:
    """Risk throttle for the ICT M5/M1 intraday research setup."""

    active_risk_percent: float = 0.50
    probation_risk_percent: float = 0.05
    rolling_window_trades: int = 20
    rolling_threshold_r: float = -3.00
    cooldown_days: int = 120
    daily_soft_brake_r: float = -2.00
    drawdown_micro_threshold_percent: float = 8.80
    recent_r: list[float] = field(default_factory=list)
    probation_until: datetime | None = None

    def risk_percent_for_trade(
        self,
        now: datetime,
        current_day_r: float,
        current_drawdown_percent: float,
    ) -> float:
        """Return the risk percent for the next trade.

        The policy intentionally returns a nonzero risk in weak periods so the
        intraday bot remains active instead of fully stopping. Severe conditions
        lower risk to the probation amount.
        """

        risk = self.active_risk_percent
        if self.probation_until is not None and now < self.probation_until:
            risk = min(risk, self.probation_risk_percent)
        if current_day_r <= self.daily_soft_brake_r:
            risk = min(risk, self.probation_risk_percent)
        if current_drawdown_percent >= self.drawdown_micro_threshold_percent:
            risk = min(risk, self.probation_risk_percent)
        return risk

    def record_trade_r(self, trade_time: datetime, r_multiple: float) -> None:
        self.recent_r.append(float(r_multiple))
        if len(self.recent_r) > self.rolling_window_trades:
            self.recent_r = self.recent_r[-self.rolling_window_trades :]
        if len(self.recent_r) == self.rolling_window_trades:
            if sum(self.recent_r) <= self.rolling_threshold_r:
                self.probation_until = trade_time + timedelta(days=self.cooldown_days)
                self.recent_r.clear()
