from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class ICTLowerDrawdownHighActivityPolicy:
    profile_id: str = "V13_ICT_LOW_DD_ACTIVE_GAP60_RESEARCH"
    normal_risk_percent: float = 0.30
    micro_risk_percent: float = 0.03
    min_gap_minutes_per_symbol: int = 60
    rolling_window_trades: int = 50
    rolling_throttle_trigger_r: float = -12.00
    cooldown_days: int = 21
    daily_soft_brake_r: float = -10.00
    drawdown_micro_trigger_percent: float = 5.00
    recent_r: list[float] = field(default_factory=list)
    micro_until: datetime | None = None

    def risk_percent_for_trade(self, now: datetime, current_day_r: float, current_drawdown_percent: float) -> float:
        risk = self.normal_risk_percent
        if self.micro_until is not None and now < self.micro_until:
            risk = min(risk, self.micro_risk_percent)
        if current_day_r <= self.daily_soft_brake_r:
            risk = min(risk, self.micro_risk_percent)
        if current_drawdown_percent >= self.drawdown_micro_trigger_percent:
            risk = min(risk, self.micro_risk_percent)
        return risk

    def record_trade_r(self, trade_time: datetime, r_multiple: float) -> None:
        self.recent_r.append(float(r_multiple))
        if len(self.recent_r) > self.rolling_window_trades:
            self.recent_r = self.recent_r[-self.rolling_window_trades:]
        if len(self.recent_r) == self.rolling_window_trades and sum(self.recent_r) <= self.rolling_throttle_trigger_r:
            self.micro_until = trade_time + timedelta(days=self.cooldown_days)
            self.recent_r.clear()
