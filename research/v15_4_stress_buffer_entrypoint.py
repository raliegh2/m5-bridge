"""V15.4 stress-buffered execution wrapper for the V15.3 portfolio.

The unchanged V14.9 baseline is measured first.  Only then is the projected
stress admission ceiling tightened from 9.95% to 9.20% for V15 portfolio
replays.  This is a stricter pre-entry risk control, not a strategy or risk
increase.
"""
from __future__ import annotations

import json
from pathlib import Path

from research import v15_3_risk_balanced_entrypoint as implementation

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "v15_4_stress_buffer_output"
PROJECTED_STRESS_LIMIT = 9.20


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
    payload["version"] = "V15.4"
    payload["risk_selection"]["v15_projected_stress_admission_limit_percent"] = PROJECTED_STRESS_LIMIT
    payload["risk_selection"]["baseline_measured_before_tightening"] = True
    payload["risk_selection"]["core_signal_definitions_changed"] = False
    payload["risk_selection"]["nominal_core_risk_changed"] = False
    (OUT / "v15_4_stress_buffer_results.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    report = OUT / "BACKTEST_REPORT.md"
    text = report.read_text(encoding="utf-8")
    text = text.replace("V15.3 Risk-Balanced", "V15.4 Stress-Buffered")
    text += (
        "\n## V15-only safety buffer\n\n"
        "After the unchanged V14.9 baseline was measured, the projected-stress "
        "admission ceiling was tightened to **9.20%** for combined V15 replays. "
        "This scales or rejects positions earlier and does not increase nominal risk.\n"
    )
    report.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
