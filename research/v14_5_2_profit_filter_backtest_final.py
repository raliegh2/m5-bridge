"""Final V14.5.2 backtest entrypoint.

Runs the common V14.5.1/V14.5.2 replay and normalizes its human-readable
metadata to the two filters that passed both development and validation:
EURUSD_SWING_CORE at 16:00 UTC and GBPJPY_SWING_CORE on Tuesday UTC.
"""
from __future__ import annotations

import json

from research.v14_5_2_profit_filter_backtest import OUT, main as run_backtest


FINAL_FILTERS = [
    "EURUSD_SWING_CORE 16UTC -> observation",
    "GBPJPY_SWING_CORE Tuesday UTC -> observation",
]


def finalize_metadata() -> None:
    result_path = OUT / "v14_5_2_results.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["models"]["v14_5_2"]["filters"] = FINAL_FILTERS
    payload["models"]["v14_5_2"]["selection_rule"] = (
        "Each filter had negative demo-cost net R and profit factor below 1.0 "
        "in both the development and validation partitions."
    )
    result_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )

    report_path = OUT / "BACKTEST_REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace(
        "It only demotes three robustly weak, pre-entry UTC time buckets",
        "It only demotes two robustly weak, pre-entry UTC time buckets",
    )
    report += (
        "\n## Final validated filters\n\n"
        "- `EURUSD_SWING_CORE` entries at 16:00 UTC use the 0.025% observation tier.\n"
        "- `GBPJPY_SWING_CORE` Tuesday UTC entries use the 0.025% observation tier.\n"
        "- EURUSD Monday was explicitly rejected because it did not remain negative in the development partition.\n"
    )
    report_path.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    run_backtest()
    finalize_metadata()
