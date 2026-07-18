"""Cost-accounting and selection correction for the V16 five-symbol replay.

The V15 generators expose both raw R and an execution reserve. Some inherited
core candidates already have that reserve deducted, while newer H1/H4/D1
candidates carry it as metadata for later deduction. This wrapper normalizes
both representations, deducts every existing reserve exactly once, applies the
additional V16 live reserve, recalculates the core walk-forward gate from the
all-in R stream, and does not force an unqualified new sleeve onto a symbol that
is already covered by the inherited five-symbol core.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v16_live_cost_five_symbol_entrypoint as model  # noqa: E402


def all_in_cost_buffer(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    """Deduct declared historical and V16 reserves exactly once per candidate."""
    work = frame.copy()
    current_r = pd.to_numeric(work["r_multiple"], errors="coerce")
    raw_r = pd.to_numeric(work.get("raw_r_multiple", current_r), errors="coerce")
    declared_cost = pd.to_numeric(
        work.get("cost_r", work.get("selection_cost_r", 0.0)), errors="coerce"
    )
    if not isinstance(declared_cost, pd.Series):
        declared_cost = pd.Series(float(declared_cost or 0.0), index=work.index)
    declared_cost = declared_cost.fillna(0.0).clip(lower=0.0)

    already_deducted = (raw_r - current_r).clip(lower=0.0)
    unapplied_declared = (declared_cost - already_deducted).clip(lower=0.0)
    live_extra = work.apply(model.extra_cost_for_row, axis=1).astype(float)

    work["pre_v16_r_multiple"] = current_r
    work["declared_cost_r"] = declared_cost
    work["already_deducted_cost_r"] = already_deducted
    work["newly_deducted_declared_cost_r"] = unapplied_declared
    work["live_cost_buffer_r"] = live_extra
    work["r_multiple"] = current_r - unapplied_declared - live_extra
    work["cost_r"] = already_deducted + unapplied_declared + live_extra
    work["v16_cost_model"] = label
    return work


def cost_aware_baseline(
    core: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    baseline, evidence = model.v15.build_baseline_candidates(core)
    baseline = all_in_cost_buffer(
        baseline, "FXCM_BID_ASK_PLUS_ALL_DECLARED_AND_V16_LIVE_RESERVES"
    )

    gate_columns = (
        "gate_active",
        "gate_reason",
        "trailing_trades",
        "trailing_net_r",
        "trailing_profit_factor",
        "trailing_expectancy_r",
        "gate_score",
        "priority_score",
    )
    baseline = baseline.drop(
        columns=[column for column in gate_columns if column in baseline],
        errors="ignore",
    )
    baseline = model.v15.v149.apply_walk_forward_gate(baseline)
    baseline["priority_score"] = pd.to_numeric(
        baseline.get("gate_score", 0.0), errors="coerce"
    ).fillna(0.0)
    baseline["requested_risk_percent"] = pd.to_numeric(
        baseline["risk_percent"], errors="coerce"
    )
    baseline["priority_class"] = 0
    return baseline, evidence


def select_only_qualified_new_profiles(
    source: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use strict pre-2016 evidence without forcing one new sleeve per symbol.

    The inherited V14.9 core already covers all five symbols. New V16 sleeves
    are optional diversifiers and are admitted only where the pre-2016 evidence
    passes the original V16 standard. Final full-period and holdout gates still
    require positive contribution from every one of the five symbols.
    """
    evidence_rows: list[dict[str, Any]] = []
    accepted_rows: list[dict[str, Any]] = []
    for sleeve_id, group in source.groupby("sleeve_id", sort=False):
        group = group.sort_values(["entry_time", "exit_time"]).copy()
        pre = group[
            (group.entry_time >= model.PRETRAIN_START)
            & (group.entry_time < model.TEST_START)
        ]
        if pre.empty:
            continue
        family = str(group.iloc[0].family)
        blocks = model.pretrain_blocks(pre)
        aggregate = model.ratio_stats(pre)
        dd_r = model.maximum_drawdown_r(pre)
        minimum = model.minimum_block_trades(family)
        positive_blocks = sum(float(item["net_r"]) > 0.0 for item in blocks.values())
        profitable_pf_blocks = sum(
            float(item["profit_factor"] or 0.0) >= 1.0 for item in blocks.values()
        )
        enough_blocks = sum(
            int(item["trades"]) >= minimum for item in blocks.values()
        )
        robust_score = (
            float(aggregate["expectancy_r"] or 0.0)
            * math.sqrt(max(1, int(aggregate["trades"])))
            * min(3.0, max(0.0, float(aggregate["profit_factor"] or 0.0)))
            / max(1.0, dd_r)
        )
        passed = (
            int(aggregate["trades"]) >= minimum * 3
            and enough_blocks >= 2
            and positive_blocks >= 2
            and profitable_pf_blocks >= 2
            and float(aggregate["net_r"]) >= 3.0
            and float(aggregate["profit_factor"] or 0.0) >= 1.15
            and float(aggregate["expectancy_r"] or 0.0) > 0.04
            and dd_r <= 7.0
        )
        record = {
            "sleeve_id": str(sleeve_id),
            "symbol": str(group.iloc[0].symbol),
            "family": family,
            "profile": str(group.iloc[0].profile),
            "timeframe": str(group.iloc[0].timeframe),
            "blocks": blocks,
            "pretrain": aggregate,
            "pretrain_maximum_drawdown_r": round(dd_r, 6),
            "positive_blocks": positive_blocks,
            "profitable_pf_blocks": profitable_pf_blocks,
            "enough_blocks": enough_blocks,
            "robust_score": float(robust_score),
            "passed": bool(passed),
        }
        evidence_rows.append(record)
        if not passed:
            continue
        pf = float(aggregate["profit_factor"] or 0.0)
        net_r = float(aggregate["net_r"])
        risk = 0.35
        if pf >= 1.35 and net_r >= 5.0:
            risk = 0.50
        if pf >= 1.60 and net_r >= 8.0 and dd_r <= 5.0:
            risk = 0.70
        if pf >= 1.90 and net_r >= 12.0 and dd_r <= 4.0:
            risk = 0.90
        accepted_rows.append({**record, "base_risk_percent": risk})

    evidence = pd.DataFrame(evidence_rows)
    if not accepted_rows:
        raise RuntimeError("No pre-2016 all-in-cost V16 profile passed selection")
    accepted = pd.DataFrame(accepted_rows).sort_values(
        ["symbol", "robust_score"], ascending=[True, False]
    )
    accepted = accepted.drop_duplicates(["symbol", "family"], keep="first")
    selected = (
        accepted.groupby("symbol", group_keys=False)
        .head(model.MAX_PROFILES_PER_SYMBOL)
        .reset_index(drop=True)
    )
    return evidence, selected


def main() -> None:
    model.apply_live_cost_buffer = all_in_cost_buffer
    model.prepare_baseline = cost_aware_baseline
    model.select_pre2016_profiles = select_only_qualified_new_profiles
    model.main()


if __name__ == "__main__":
    main()
