from __future__ import annotations

import json

from research import v14_11_macro_carry_confirmed_entrypoint as confirmed

study = confirmed.study
_original_gate = study.apply_carry_gate


def strict_carry_gate(frame):
    output = _original_gate(frame)
    active = (
        output["gate_active"]
        & (output["trailing_net_r"] >= 0.50)
        & (output["trailing_profit_factor"] >= 1.20)
    )
    output["gate_active"] = active
    output["gate_reason"] = active.map(
        {True: "CARRY_STRICT_TRAILING_EDGE_ACTIVE", False: "CARRY_STRICT_TRAILING_EDGE_INACTIVE"}
    )
    return output


study.apply_carry_gate = strict_carry_gate


if __name__ == "__main__":
    confirmed._build_gated_v149_candidates = confirmed.build_existing
    study.main()
    path = study.OUT / "v14_11_macro_carry_results.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["selection_protocol"] = {
        "confirmation_used_for_selection": True,
        "untouched_out_of_sample_claimed": False,
        "carry_trailing_minimum_net_r": 0.50,
        "carry_trailing_minimum_profit_factor": 1.20,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    report_path = study.OUT / "BACKTEST_REPORT.md"
    report_path.write_text(
        report_path.read_text(encoding="utf-8")
        + "\n## Strict carry activation\n\nMacro carry requires at least 0.50R trailing net profit and PF 1.20 before capital is allocated.\n",
        encoding="utf-8",
    )
