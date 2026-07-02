"""Frozen USDJPY quality gate for the V13 expanded-assets engine.

The existing D1/H4 40-bar breakout signal remains authoritative. This module
adds two completed-candle admission checks selected on the original development
segment and confirmed on the untouched validation segment:

* completed H4 body ratio must be at least 0.30;
* signal-end hour must be 08:00, 12:00, 16:00 or 20:00 UTC.

Risk, stop, target, trailing and holding-period settings are unchanged. The
module is deterministic so live evaluation and backtests use exactly the same
rule.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class USDJPYQualityConfig:
    minimum_body_ratio: float = 0.30
    allowed_signal_end_hours_utc: tuple[int, ...] = (8, 12, 16, 20)

    @classmethod
    def from_env(cls) -> "USDJPYQualityConfig":
        hours = tuple(
            int(item.strip())
            for item in os.getenv(
                "USDJPY_ALLOWED_SIGNAL_HOURS_UTC", "8,12,16,20"
            ).split(",")
            if item.strip()
        )
        config = cls(
            minimum_body_ratio=float(
                os.getenv("USDJPY_MIN_BODY_RATIO", "0.30")
            ),
            allowed_signal_end_hours_utc=hours,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not 0 < self.minimum_body_ratio <= 1.0:
            raise ValueError("USDJPY_MIN_BODY_RATIO must be within (0, 1]")
        if not self.allowed_signal_end_hours_utc:
            raise ValueError("USDJPY allowed-hour list cannot be empty")
        if any(hour < 0 or hour > 23 for hour in self.allowed_signal_end_hours_utc):
            raise ValueError("USDJPY allowed hours must be valid UTC hours")
        if len(set(self.allowed_signal_end_hours_utc)) != len(
            self.allowed_signal_end_hours_utc
        ):
            raise ValueError("USDJPY allowed hours must not contain duplicates")


@dataclass(frozen=True)
class USDJPYQualityDecision:
    allowed: bool
    reason: str


def evaluate_usdjpy_quality(
    *,
    signal_end: datetime,
    body_ratio: float,
    config: USDJPYQualityConfig | None = None,
) -> USDJPYQualityDecision:
    """Evaluate the frozen quality rules on a completed H4 signal candle."""
    config = config or USDJPYQualityConfig.from_env()
    config.validate()
    if signal_end.hour not in config.allowed_signal_end_hours_utc:
        return USDJPYQualityDecision(
            False,
            "USDJPY signal skipped: signal-end hour is outside the validated "
            f"UTC set {config.allowed_signal_end_hours_utc}.",
        )
    if float(body_ratio) < config.minimum_body_ratio:
        return USDJPYQualityDecision(
            False,
            "USDJPY signal skipped: completed H4 body ratio "
            f"{float(body_ratio):.3f} is below {config.minimum_body_ratio:.3f}.",
        )
    return USDJPYQualityDecision(
        True,
        "USDJPY signal passed the validated time and H4 body-quality gate.",
    )
