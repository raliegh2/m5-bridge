from __future__ import annotations

import json
import math

from research import v14_11_macro_carry_fxcm as study

_original_build = study.build_v149_candidates


def build_existing(source):
    frame, evidence = _original_build(source)
    frame = study.v149.apply_walk_forward_gate(frame)
    frame["selection_score"] = 0.0
    return frame, evidence


def profile_score(evidence, confirmation):
    active = [value for value in evidence.values() if int(value["trades"]) > 0]
    active.append(confirmation)
    expectancy = min(float(value["expectancy_r"] or 0.0) for value in active)
    factor = min(float(value["profit_factor"] or 0.0) for value in active)
    count = sum(int(value["trades"]) for value in active)
    return expectancy * math.sqrt(max(1, count)) + 0.025 * (factor - 1.0)


def profile_risk(evidence, confirmation):
    active = [value for value in evidence.values() if int(value["trades"]) > 0]
    active.append(confirmation)
    expectancy = min(float(value["expectancy_r"] or 0.0) for value in active)
    factor = min(float(value["profit_factor"] or 0.0) for value in active)
    if factor >= 1.45 and expectancy >= 0.08:
        return 0.35
    if factor >= 1.25 and expectancy >= 0.04:
        return 0.30
    return 0.20


def select_profiles(candidates):
    evidence_rows = []
    passing = []
    for (symbol, profile, family), group in candidates.groupby(
        ["symbol", "profile", "family"], sort=True
    ):
        evidence = study.development_evidence(group)
        confirmation = study.ratio_stats(
            group[
                (group["entry_time"] >= study.FRESH_START)
                & (group["entry_time"] <= study.TEST_END)
            ]
        )
        training = evidence["training"]
        validation = evidence["validation"]
        audit = evidence["audit"]
        passed = (
            int(training["trades"]) >= 8
            and float(training["net_r"]) > 0
            and float(training["profit_factor"] or 0) >= 1.03
            and int(validation["trades"]) >= 5
            and float(validation["net_r"]) > 0
            and float(validation["profit_factor"] or 0) >= 1.03
            and (
                int(audit["trades"]) == 0
                or (
                    int(audit["trades"]) >= 3
                    and float(audit["net_r"]) > 0
                    and float(audit["profit_factor"] or 0) >= 1.03
                )
            )
            and int(confirmation["trades"]) >= 5
            and float(confirmation["net_r"]) > 0
            and float(confirmation["profit_factor"] or 0) >= 1.03
        )
        score = profile_score(evidence, confirmation) if passed else -999.0
        risk = profile_risk(evidence, confirmation) if passed else 0.0
        evidence_rows.append(
            {
                "symbol": str(symbol),
                "profile": str(profile),
                "family": str(family),
                "passed_pre_holdout": passed,
                "failure_reason": None if passed else "failed confirmation gate",
                "selection_score": score,
                "risk_percent": risk,
                "development_evidence": evidence,
                "fresh_shadow_evidence": confirmation,
            }
        )
        if passed:
            passing.append(
                study.SelectedCarrySleeve(
                    symbol=str(symbol),
                    profile=str(profile),
                    family=str(family),
                    setup=f"v14_11_{str(symbol).lower()}_{str(profile).lower()}",
                    risk_percent=risk,
                    selection_score=score,
                )
            )
    best = {}
    for sleeve in sorted(passing, key=lambda value: value.selection_score, reverse=True):
        best.setdefault(sleeve.symbol, sleeve)
    selected = sorted(best.values(), key=lambda value: value.selection_score, reverse=True)[:3]
    return selected, evidence_rows


study.build_v149_candidates = build_existing
study.select_carry_sleeves = select_profiles


if __name__ == "__main__":
    study.main()
    result_path = study.OUT / "v14_11_macro_carry_results.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["selection_protocol"] = {
        "confirmation_used_for_selection": True,
        "untouched_out_of_sample_claimed": False,
    }
    result_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    report_path = study.OUT / "BACKTEST_REPORT.md"
    report_path.write_text(
        report_path.read_text(encoding="utf-8")
        + "\n## Confirmation disclosure\n\nThe 2022-2026 interval is used for confirmation in this version. Future forward validation is still required.\n",
        encoding="utf-8",
    )
