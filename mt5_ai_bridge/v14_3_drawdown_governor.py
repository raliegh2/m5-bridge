"""Frozen research governor for the five-symbol V12 + V14.3 portfolio.

The governor limits clustered equity losses without changing any signal rule:

* ICT entries pause for 72 hours when closed-equity drawdown reaches 6%;
* after the pause, ICT entries run at 30% risk until drawdown recovers below 4%;
* approved ICT risk tiers receive only a 5% normal-state uplift;
* four high-quality V12 engines receive a bounded 1.55x allocation;
* every individual trade remains capped below 0.80% account risk.

This module is research/demo infrastructure. It never connects to MT5 or places
orders.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from mt5_ai_bridge.v14_3_profit_preserving_profile import (
    PORTFOLIO_GUARD,
    SYMBOL_GUARDS,
    base_risk_percent,
)


V12_ENGINE_MULTIPLIERS: dict[str, float] = {
    "GBPUSD_V10_PRECISION": 1.55,
    "EURUSD_SWING_CORE": 1.55,
    "AUDUSD_TREND_PULLBACK": 1.55,
    "GBPJPY_SWING_CORE": 1.55,
}
V12_DEFAULT_MULTIPLIER = 1.00
ICT_NORMAL_MULTIPLIER = 1.05
MAX_V12_TRADE_RISK_PERCENT = 0.78
MAX_ICT_TRADE_RISK_PERCENT = 0.80


@dataclass(frozen=True)
class DrawdownGovernorConfig:
    trigger_percent: float = 6.0
    release_percent: float = 4.0
    pause_hours: float = 72.0
    recovery_risk_multiplier: float = 0.30


CONFIG = DrawdownGovernorConfig()


@dataclass
class DrawdownGovernorState:
    armed: bool = True
    pause_until: pd.Timestamp | None = None
    trigger_count: int = 0

    def observe(self, now: pd.Timestamp, drawdown_percent: float) -> None:
        """Advance state using information known before the next entry."""
        drawdown = max(0.0, float(drawdown_percent))
        if not self.armed and drawdown <= CONFIG.release_percent:
            self.armed = True
            self.pause_until = None
        if self.armed and drawdown >= CONFIG.trigger_percent:
            self.armed = False
            self.pause_until = now + pd.Timedelta(hours=CONFIG.pause_hours)
            self.trigger_count += 1

    def in_pause(self, now: pd.Timestamp) -> bool:
        return self.pause_until is not None and now < self.pause_until

    def recovery_multiplier(self, now: pd.Timestamp) -> float:
        if not self.armed and not self.in_pause(now):
            return CONFIG.recovery_risk_multiplier
        return 1.0

    def phase(self, now: pd.Timestamp) -> str:
        if self.armed:
            return "NORMAL"
        if self.in_pause(now):
            return "PAUSE"
        return "RECOVERY"


def adjusted_v12_risk_percent(engine: str, original_risk_percent: float) -> tuple[float, str]:
    """Return bounded V12 risk and an auditable allocation tier."""
    original = max(0.0, float(original_risk_percent))
    multiplier = V12_ENGINE_MULTIPLIERS.get(str(engine), V12_DEFAULT_MULTIPLIER)
    adjusted = min(original * multiplier, MAX_V12_TRADE_RISK_PERCENT)
    tier = "V12_QUALITY_155" if multiplier > 1.0 else "V12_UNCHANGED"
    return adjusted, tier


def adjusted_ict_risk_percent(
    symbol: str,
    setup: str,
    pre_entry_drawdown_percent: float,
    under_loss_pressure: bool,
    recovery_multiplier: float = 1.0,
) -> float:
    """Apply the five-symbol ICT allocation before recovery-state scaling."""
    symbol = symbol.upper()
    guard = SYMBOL_GUARDS[symbol]
    risk = base_risk_percent(symbol, setup) * ICT_NORMAL_MULTIPLIER
    if under_loss_pressure:
        risk *= guard.post_loss_multiplier

    drawdown = max(0.0, float(pre_entry_drawdown_percent))
    if drawdown > PORTFOLIO_GUARD.drawdown_scale_start_percent:
        floor = min(risk, PORTFOLIO_GUARD.drawdown_risk_floor_percent)
        if drawdown >= PORTFOLIO_GUARD.drawdown_scale_end_percent:
            risk = floor
        else:
            span = (
                PORTFOLIO_GUARD.drawdown_scale_end_percent
                - PORTFOLIO_GUARD.drawdown_scale_start_percent
            )
            fraction = (
                drawdown - PORTFOLIO_GUARD.drawdown_scale_start_percent
            ) / span
            risk = risk * (1.0 - fraction) + floor * fraction

    risk *= max(0.0, float(recovery_multiplier))
    return min(risk, MAX_ICT_TRADE_RISK_PERCENT)


def validate_governor() -> None:
    if not 0.0 < CONFIG.release_percent < CONFIG.trigger_percent < 10.0:
        raise RuntimeError("Drawdown trigger and release thresholds are invalid")
    if CONFIG.pause_hours < 24.0:
        raise RuntimeError("Drawdown pause must span at least one trading day")
    if not 0.0 < CONFIG.recovery_risk_multiplier < 1.0:
        raise RuntimeError("Recovery risk multiplier must reduce risk")
    if max(V12_ENGINE_MULTIPLIERS.values()) > 1.55:
        raise RuntimeError("V12 quality uplift exceeds the frozen bound")
    if MAX_V12_TRADE_RISK_PERCENT >= 0.80 or MAX_ICT_TRADE_RISK_PERCENT > 0.80:
        raise RuntimeError("Per-trade risk caps exceed the approved research limit")


validate_governor()
