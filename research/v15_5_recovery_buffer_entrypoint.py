"""V15.5 recovery-capable stress-buffered V15 replay.

The unchanged V14.9 baseline is measured first. Combined V15 replays then use
a 9.40% projected-stress admission ceiling. This retains a 0.20 percentage
point buffer to the 9.60% hard stop while avoiding the permanent lockout that
can occur when the stress ceiling sits below a realized closed drawdown.
"""
from __future__ import annotations

import json
from pathlib import Path

from research import v15_3_risk_balanced_entrypoint as implementation

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v15_5_recovery_buffer_output"
PROJECTED_STRESS_LIMIT = 9.40


def main() -> None:
    implementation.OUT = OUT
    original_baseline_replay = implementation.v15.baseline_replay

    def baseline_then_tighten(baseline):
        result = original_baseline_replay(baseline)
        implementation.v15.v149.v148.PROJECTED_STRESS_LIMIT = PROJECTED_STRESS_LIMIT
        return result

    implementation.v15.baseline_replay = baseline_then_tighten
    implementation.main()

    old_json = OUT / "v15_3_risk_balanced_results.json"
    payload = json.loads(old_json.read_text(encoding="utf-8"))
    payload["version"] = "V15.5"
    payload["risk_selection"]["v15_projected_stress_admission_limit_percent"] = PROJECTED_STRESS_LIMIT
    payload["risk_selection"]["buffer_to_closed_hard_stop_percentage_points"] = 0.20
    payload["risk_selection"]["baseline_measured_before_tightening"] = True
    payload["risk_selection"]["core_signal_definitions_changed"] = False
    payload["risk_selection"]["nominal_core_risk_changed"] = False
    payload["forward_holdout_passed"] = (
        float(payload["forward_2024_2026_combined"]["net_profit"]) > 0.0
        and float(payload["forward_2024_2026_combined"]["profit_factor"] or 0.0) > 1.0
    )
    payload["promotion_eligible"] = bool(
        payload["portfolio"]["safe"]
        and payload["forward_deployment_portfolio"]["safe"]
        and payload["new_system_contribution"]["net_profit"] > 0.0
        and payload["forward_holdout_passed"]
    )
    (OUT / "v15_5_recovery_buffer_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    report = OUT / "BACKTEST_REPORT.md"
    text = report.read_text(encoding="utf-8")
    text = text.replace("V15.3 Risk-Balanced", "V15.5 Recovery-Buffered")
    text += (
        "\n## V15-only recovery buffer\n\n"
        "After measuring the unchanged V14.9 baseline, combined V15 replays use "
        "a **9.40%** projected-stress admission ceiling. This preserves a 0.20 "
        "percentage-point buffer to the 9.60% closed-drawdown hard stop while "
        "allowing the portfolio to recover after a loss.\n\n"
        f"Forward-holdout passed: **{payload['forward_holdout_passed']}**.  "
        f"Promotion eligible: **{payload['promotion_eligible']}**.\n"
    )
    report.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
