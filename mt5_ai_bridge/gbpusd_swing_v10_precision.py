"""Precision timing policy for the V10 GBPUSD swing engine.

The policy uses only completed signal-bar information. It does not attempt to
predict the exact market top or bottom. Instead, it concentrates risk on the
historically strongest breakout and pullback conditions while reducing or
rejecting overextended entries.
"""
from __future__ import annotations

from dataclasses import dataclass

PRIMARY_SETUP = "PRIMARY_16UTC_BREAKOUT"
SECONDARY_SETUP = "SECONDARY_12UTC_BREAKOUT"
PULLBACK_SETUP = "GBPUSD_SWING_V5_PULLBACK_ADDON"


@dataclass(frozen=True)
class SwingPrecisionParams:
    primary_a_volume_ratio_min: float = 1.248
    primary_a_range_atr_min: float = 1.555
    primary_a_risk_percent: float = 0.50
    primary_b_risk_percent: float = 0.20
    secondary_atr_ratio_min: float = 1.018
    secondary_directional_body_atr_max: float = 1.473
    secondary_risk_percent: float = 0.40
    pullback_directional_ema_gap_atr_max: float = 1.237
    pullback_risk_percent: float = 0.40

    def validate(self) -> None:
        risks = (
            self.primary_a_risk_percent,
            self.primary_b_risk_percent,
            self.secondary_risk_percent,
            self.pullback_risk_percent,
        )
        if any(risk <= 0 or risk > 0.50 for risk in risks):
            raise ValueError("Swing precision risks must be within (0, 0.50]")
        if self.primary_b_risk_percent > self.primary_a_risk_percent:
            raise ValueError("Primary B risk cannot exceed Primary A risk")
        if self.primary_a_volume_ratio_min <= 0:
            raise ValueError("Volume threshold must be positive")
        if self.primary_a_range_atr_min <= 0:
            raise ValueError("Range/ATR threshold must be positive")


@dataclass(frozen=True)
class SwingTimingDecision:
    allowed: bool
    grade: str
    risk_percent: float
    reason: str
    directional_body_atr: float
    directional_ema_gap_atr: float


def normalize_setup(setup: str) -> str:
    normalized = setup.upper().strip()
    if normalized.startswith("GBPUSD_V4_"):
        normalized = normalized.removeprefix("GBPUSD_V4_")
    return normalized


def evaluate_swing_timing(
    *,
    setup: str,
    side: int,
    open_price: float,
    close_price: float,
    atr14: float,
    volume_ratio: float,
    range_atr: float,
    atr_ratio: float,
    ema20_h4: float,
    ema50_h4: float,
    params: SwingPrecisionParams = SwingPrecisionParams(),
) -> SwingTimingDecision:
    """Return the V10 precision admission decision and risk tier.

    All arguments must describe the completed signal candle. ``side`` is 1 for
    long and -1 for short. The function is deterministic and has no clock or
    broker dependency, so live execution and backtests can use the same rules.
    """
    params.validate()
    if side not in {-1, 1}:
        raise ValueError("side must be 1 or -1")
    if atr14 <= 0:
        raise ValueError("atr14 must be positive")

    normalized = normalize_setup(setup)
    directional_body_atr = side * (close_price - open_price) / atr14
    directional_ema_gap_atr = side * (ema20_h4 - ema50_h4) / atr14

    if normalized == PRIMARY_SETUP:
        a_grade = (
            volume_ratio >= params.primary_a_volume_ratio_min
            and range_atr >= params.primary_a_range_atr_min
        )
        if a_grade:
            return SwingTimingDecision(
                True,
                "A",
                params.primary_a_risk_percent,
                "Strong volume expansion and decisive H4 range expansion.",
                directional_body_atr,
                directional_ema_gap_atr,
            )
        return SwingTimingDecision(
            True,
            "B",
            params.primary_b_risk_percent,
            "Valid primary breakout, but without the A-grade expansion profile.",
            directional_body_atr,
            directional_ema_gap_atr,
        )

    if normalized == SECONDARY_SETUP:
        allowed = (
            atr_ratio >= params.secondary_atr_ratio_min
            and directional_body_atr
            <= params.secondary_directional_body_atr_max
        )
        return SwingTimingDecision(
            allowed,
            "A" if allowed else "REJECT",
            params.secondary_risk_percent if allowed else 0.0,
            (
                "Controlled continuation candle with sufficient volatility expansion."
                if allowed
                else "Secondary breakout is overextended or lacks volatility expansion."
            ),
            directional_body_atr,
            directional_ema_gap_atr,
        )

    if normalized == PULLBACK_SETUP:
        allowed = (
            directional_ema_gap_atr
            <= params.pullback_directional_ema_gap_atr_max
        )
        return SwingTimingDecision(
            allowed,
            "A" if allowed else "REJECT",
            params.pullback_risk_percent if allowed else 0.0,
            (
                "Trend is aligned without excessive H4 EMA separation."
                if allowed
                else "Pullback entry is too extended from the H4 trend mean."
            ),
            directional_body_atr,
            directional_ema_gap_atr,
        )

    raise ValueError(f"Unsupported swing setup: {setup}")
