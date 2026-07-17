from __future__ import annotations

import json

from research import v14_11_macro_carry_confirmed_entrypoint as confirmed

study = confirmed.study
_original_gate = study.apply_carry_gate
_original_select = confirmed.select_profiles


def select_micro_carry(candidates):
    selected, evidence = _original_select(candidates)
    audusd = [item for item in selected if item.symbol == "AUDUSD"]
    if not audusd:
        raise RuntimeError("Confirmed AUDUSD carry sleeve was not available")
    original = audusd[0]
    micro = study.SelectedCarrySleeve(
        symbol=original.symbol,
        profile=original.profile,
        family=original.family,
        setup=original.setup,
        risk_percent=0.10,
        selection_score=original.selection_score,
    )
    return [micro], evidence


def micro_carry_gate(frame):
    output = _original_gate(frame)
    active = (
        output["gate_active"]
        & (output["trailing_net_r"] >= 0.50)
        & (output["trailing_profit_factor"] >= 1.20)
    )
    output["gate_active"] = active
    output["gate_reason"] = active.map(
        {True: "MICRO_CARRY_EDGE_ACTIVE", False: "MICRO_CARRY_EDGE_INACTIVE"}
    )
    return output


study.build_v149_candidates = confirmed.build_existing
study.select_carry_sleeves = select_micro_carry
study.apply_carry_gate = micro_carry_gate


if __name__ == "__main__":
    study.main()
    path = study.OUT / "v14_11_macro_carry_results.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["selection_protocol"] = {
        "confirmation_used_for_selection": True,
        "untouched_out_of_sample_claimed": False,
        "selected_macro_symbol": "AUDUSD",
        "macro_risk_percent": 0.10,
        "carry_trailing_minimum_net_r": 0.50,
        "carry_trailing_minimum_profit_factor": 1.20,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    report_path = study.OUT / "BACKTEST_REPORT.md"
    report_path.write_text(
        report_path.read_text(encoding="utf-8")
        + "\n## Micro-carry allocation\n\nOnly the strongest confirmed AUDUSD macro-carry sleeve receives capital, capped at 0.10% risk per trade. This full-history confirmation fit still requires new forward validation.\n",
        encoding="utf-8",
    )
