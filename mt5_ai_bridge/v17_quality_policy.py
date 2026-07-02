"""Frozen quality gates for the V17 five-symbol swing portfolio.

These filters were selected with chronological development, validation and
holdout checks. They never inspect a future candle. The policy controls signal
admission only; it does not increase the existing portfolio risk limits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class QualityDecision:
    allowed: bool
    reason: str


# Python weekday: Monday=0 ... Sunday=6.
QUALITY_WINDOWS: dict[str, tuple[frozenset[int], frozenset[int]]] = {
    "EURUSD_H4_VALIDATED": (
        frozenset({0, 12, 20}),
        frozenset({2, 3, 4}),
    ),
    "GBPJPY_H4_VALIDATED": (
        frozenset({0, 8, 12, 16}),
        frozenset({0, 2, 3}),
    ),
    "GBPUSD_SWING_RETEST": (
        frozenset({0, 12, 20}),
        frozenset({0, 2, 4}),
    ),
    "GBPUSD_SWING_CORE": (
        frozenset({0, 8, 12, 16}),
        frozenset({0, 4}),
    ),
}

PASSTHROUGH_ENGINES = frozenset({
    "GBPUSD_V10_PRECISION",
    "AUDUSD_TREND_PULLBACK_04_08UTC",
    "USDJPY_H4_QUALITY_FILTERED",
})


def evaluate_quality_window(engine: str, signal_end: datetime) -> QualityDecision:
    """Return the deterministic completed-candle admission decision."""
    if engine in PASSTHROUGH_ENGINES:
        return QualityDecision(True, "validated engine passed unchanged")
    window = QUALITY_WINDOWS.get(engine)
    if window is None:
        return QualityDecision(False, f"unsupported V17 engine: {engine}")
    hours, weekdays = window
    if signal_end.hour not in hours:
        return QualityDecision(False, "signal-end hour outside frozen quality window")
    if signal_end.weekday() not in weekdays:
        return QualityDecision(False, "weekday outside frozen quality window")
    return QualityDecision(True, "signal passed frozen hour and weekday quality window")


def symbol_risk_cap_percent(symbol: str) -> float:
    """Allow all GBPUSD sub-engines to share the dedicated 0.65% symbol cap."""
    return 0.65 if symbol.upper() == "GBPUSD" else 0.40
