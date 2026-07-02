"""Research profile for a higher-income GBPUSD swing engine.

The V4 entry rules remain frozen. This profile changes only risk and position
management. It must remain disabled until the synchronized portfolio and forward
validation gates pass.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class GBPUSDSwingV6Runner:
    enabled: bool = False

    # Maximum allowed risk requested for the swing engine.
    core_risk_percent: float = 0.50
    pullback_addon_risk_percent: float = 0.50

    # Keep more of the winning position for the trend runner.
    partial_r: float = 1.0
    partial_fraction: float = 0.33
    target_r: float = 4.5
    trail_start_r: float = 2.0
    trail_atr: float = 3.5
    max_hold_h4_bars: int = 108  # about 18 trading days

    # Existing stop controls remain in force.
    stop_atr: float = 1.5
    min_stop_pips: float = 20.0
    max_stop_pips: float = 150.0

    mode: str = "READ_ONLY"


def v4_management_overrides() -> dict:
    """Return the tested management values for the frozen V4 entry engine."""
    profile = GBPUSDSwingV6Runner()
    return {
        "risk_percent": profile.core_risk_percent,
        "partial_r": profile.partial_r,
        "partial_fraction": profile.partial_fraction,
        "target_r": profile.target_r,
        "trail_start_r": profile.trail_start_r,
        "trail_atr": profile.trail_atr,
        "max_hold_h4_bars": profile.max_hold_h4_bars,
        "stop_atr": profile.stop_atr,
        "min_stop_pips": profile.min_stop_pips,
        "max_stop_pips": profile.max_stop_pips,
    }
