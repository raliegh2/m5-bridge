"""Configuration for the Strategy Engine V8 portfolio research simulator."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioV8Config:
    initial_balance: float = 5_000.0
    max_positions: int = 3
    max_open_risk_percent: float = 0.75
    gbp_aligned_risk_percent: float = 0.75
    gbp_mixed_risk_percent: float = 0.50
    daily_loss_limit: float = 250.0
    weekly_loss_percent: float = 4.0
    total_loss_limit: float = 500.0
    drawdown_throttle_percent: float = 6.0
    drawdown_pause_percent: float = 10.0
    allow_aligned_gbpusd_engines: bool = True


ENGINE_PRIORITY = {
    "GBPUSD_SWING_V6": 0,
    "GBPUSD_SATELLITE_V2": 1,
    "EURUSD_SATELLITE_V7": 2,
    "GBPJPY_SATELLITE_V7": 3,
}
