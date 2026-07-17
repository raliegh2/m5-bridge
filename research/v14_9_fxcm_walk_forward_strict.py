"""Strict risk-bound entrypoint for V14.9.

This wrapper does not change strategy selection, filters, trailing-edge gates, or
risk percentages. It lowers the projected stressed-equity admission ceiling
from 9.95% to 9.45% so closed drawdown has room to remain below the retained
9.60% hard boundary when an admitted trade realizes a full loss.
"""
from __future__ import annotations

import json

from research import v14_8_strict_all_ten_20k as v148
from research import v14_9_fxcm_walk_forward as study

STRICT_PROJECTED_STRESS_LIMIT = 9.45


if __name__ == "__main__":
    original_limit = v148.PROJECTED_STRESS_LIMIT
    v148.PROJECTED_STRESS_LIMIT = STRICT_PROJECTED_STRESS_LIMIT
    try:
        study.main()
    finally:
        v148.PROJECTED_STRESS_LIMIT = original_limit

    result_path = study.OUT / "v14_9_fxcm_walk_forward_results.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["risk_limits"]["projected_stress_admission_limit_percent"] = STRICT_PROJECTED_STRESS_LIMIT
    payload["portfolio"]["safe"] = (
        float(payload["portfolio"]["max_closed_drawdown_percent"]) <= 9.60
        and float(payload["portfolio"]["stress_drawdown_percent"]) <= 10.00
    )
    result_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    report_path = study.OUT / "BACKTEST_REPORT.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace(
        "The existing 7.5/8.5/9.0/9.6 drawdown governor and projected-stress admission limit remain active.",
        "The existing 7.5/8.5/9.0/9.6 drawdown governor remains active, with a stricter 9.45% projected-stress admission ceiling.",
    )
    report_path.write_text(report, encoding="utf-8")
