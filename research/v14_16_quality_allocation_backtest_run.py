"""Input-normalized entry point for the V14.16 exact replay.

The historical GBP ICT source does not carry a ``risk_percent`` column because
its nominal setup allocation is installed in ``SETUP_RISK_PERCENT`` at runtime.
V14.16 records nominal risk explicitly for validation, so this entry point adds
the column from the same frozen profile before invoking the replay.
"""
from __future__ import annotations

import research.v14_16_quality_allocation_backtest as study
from mt5_ai_bridge.v14_3_profit_preserving_profile import (
    SETUP_RISK_PERCENT,
    scaled_risk_percent,
)


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
        return super().run()


def main() -> None:
    study.QualityAllocationReplay = InputNormalizedQualityAllocationReplay
    study.main()


if __name__ == "__main__":
    main()
