"""Input-normalized, stress-buffered entry point for the V14.16 replay.

The historical GBP ICT source does not carry a ``risk_percent`` column because
its nominal setup allocation is installed at runtime. V14.16 also reserves
projected drawdown capacity before a 0.80% quality allocation and compares V12
candidates against frozen full-strength setup tiers so prior reductions remain
untouched.
"""
from __future__ import annotations

import research.v14_16_quality_allocation_backtest as study
from mt5_ai_bridge.v14_3_profit_preserving_profile import (
    SETUP_RISK_PERCENT,
    scaled_risk_percent,
)
from mt5_ai_bridge.v14_16_quality_allocation import QUALITY_RISK_PERCENT
from mt5_ai_bridge.v14_16_quality_nominal import strict_quality_risk_target

QUALITY_PROJECTED_STRESS_LIMIT_PERCENT = 9.40


class StressBufferedGovernor:
    """Apply the original governor after reserving worst-case open-stop capacity."""

    def __init__(self, base, replay) -> None:
        self._base = base
        self._replay = replay
        self.soft_start_percent = base.soft_start_percent
        self.medium_start_percent = base.medium_start_percent
        self.defensive_start_percent = base.defensive_start_percent
        self.hard_stop_percent = base.hard_stop_percent
        self.soft_multiplier = base.soft_multiplier
        self.medium_multiplier = base.medium_multiplier
        self.defensive_multiplier = base.defensive_multiplier
        self.minimum_risk_percent = base.minimum_risk_percent
        self.quality_projected_stress_limit_percent = (
            QUALITY_PROJECTED_STRESS_LIMIT_PERCENT
        )

    def multiplier(self, drawdown_percent: float) -> float:
        return self._base.multiplier(drawdown_percent)

    def apply(self, risk_percent: float, drawdown_percent: float) -> float:
        requested = max(0.0, float(risk_percent))
        # Only V14.16 quality requests reach 0.80%; existing strategy tiers are
        # left unchanged. Worst-case equity assumes every currently open stop and
        # the proposed trade are lost before another position closes.
        if requested >= QUALITY_RISK_PERCENT - 1e-12:
            balance = max(0.0, float(self._replay.balance))
            peak = max(balance, float(self._replay.peak))
            active_risk = sum(
                max(0.0, float(item.get("risk_dollars", 0.0)))
                for item in self._replay.active
            )
            floor_equity = peak * (
                1.0 - QUALITY_PROJECTED_STRESS_LIMIT_PERCENT / 100.0
            )
            allowed_dollars = max(0.0, balance - active_risk - floor_equity)
            allowed_percent = (
                allowed_dollars / balance * 100.0 if balance > 0 else 0.0
            )
            requested = min(requested, allowed_percent)
            if requested < self.minimum_risk_percent - 1e-12:
                return 0.0
        return self._base.apply(requested, drawdown_percent)


class InputNormalizedQualityAllocationReplay(study.QualityAllocationReplay):
    def run(self):
        if "risk_percent" not in self.ict.columns:
            frame = self.ict.copy()
            frame["risk_percent"] = [
                float(
                    SETUP_RISK_PERCENT.get(
                        (str(row["symbol"]), str(row["setup"])),
                        scaled_risk_percent(
                            str(row["symbol"]),
                            str(row["setup"]),
                            0.0,
                            False,
                        ),
                    )
                )
                for row in frame.to_dict("records")
            ]
            self.ict = frame

        # The replay module resolves this function at runtime. Substitute the
        # strict wrapper so already-reduced V12 candidates cannot be promoted.
        study.quality_risk_target = strict_quality_risk_target
        base_governor = self.governor
        self.governor = StressBufferedGovernor(base_governor, self)
        summary, trades, skipped = super().run()
        summary["drawdown_governor"] = {
            **base_governor.__dict__,
            "quality_projected_stress_limit_percent": (
                QUALITY_PROJECTED_STRESS_LIMIT_PERCENT
            ),
        }
        return summary, trades, skipped


def main() -> None:
    study.quality_risk_target = strict_quality_risk_target
    study.QualityAllocationReplay = InputNormalizedQualityAllocationReplay
    study.main()


if __name__ == "__main__":
    main()
