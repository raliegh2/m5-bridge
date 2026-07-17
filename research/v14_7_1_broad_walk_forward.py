"""V14.7.1 broadened walk-forward selection.

V14.7 proved that the strategy families can materially improve portfolio profit,
but its pre-audit pool retained only eight high training-score components. That
excluded several stable candidates that were positive in every chronological
period. This wrapper broadens training-qualified candidates before audit
selection while leaving the final holdout untouched.
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from research import v14_7_five_symbol_20k_backtest as study  # noqa: E402

OUT = ROOT / "research" / "v14_7_1_five_symbol_20k_output"


def pass_learning(stats: dict[str, dict[str, Any]], mode: str) -> bool:
    minimum = {
        "SWING": {"train": 8, "validation": 4},
        "ICT": {"train": 12, "validation": 6},
    }[mode]
    for name in ("train", "validation"):
        item = stats[name]
        if item["trades"] < minimum[name] or item["net_r"] <= 0:
            return False
        if float(item["profit_factor"] or 0.0) <= 1.01:
            return False
    return True


def component_candidates(frame, periods, mode: str):
    rows: list[dict[str, Any]] = []
    for spec in study.base.filter_candidates(frame):
        selected = study.base.filter_frame(frame, spec)
        stats = study.stats_by_block(selected, periods)
        if not pass_learning(stats, mode):
            continue
        rows.append(
            {
                "spec": asdict(spec),
                "engine": spec.engine,
                "family": str(selected["family"].iloc[0]) if not selected.empty else "UNKNOWN",
                "stats": stats,
                "learning_score": round(study.learning_score(stats), 6),
                "frame": selected,
            }
        )
    rows.sort(key=lambda item: item["learning_score"], reverse=True)
    return rows[:60]


def ensemble_candidates(components, periods, mode: str):
    if not components:
        return []
    rows: list[dict[str, Any]] = []
    pools = {1: components[:60], 2: components[:24], 3: components[:12]}
    for size in (1, 2, 3):
        for combo in itertools.combinations(pools[size], size):
            engines = [item["engine"] for item in combo]
            if len(set(engines)) != len(engines):
                continue
            combined = study.combine_frames([item["frame"] for item in combo], mode)
            stats = study.stats_by_block(combined, periods)
            if not pass_learning(stats, mode) or not study.pass_audits(stats, mode):
                continue
            rows.append(
                {
                    "components": [item["spec"] for item in combo],
                    "engines": engines,
                    "families": [item["family"] for item in combo],
                    "stats": stats,
                    "audit_score": round(study.audit_score(stats), 6),
                    "frame": combined,
                }
            )
    rows.sort(key=lambda item: item["audit_score"], reverse=True)
    return rows


def risk_from_stats(stats: dict[str, dict[str, Any]], mode: str) -> float:
    items = list(stats.values())
    min_pf = min(float(item["profit_factor"] or 0.0) for item in items)
    min_exp = min(float(item["expectancy_r"] or 0.0) for item in items)
    if mode == "SWING":
        if min_pf >= 1.50 and min_exp >= 0.15:
            return 1.00
        if min_pf >= 1.30 and min_exp >= 0.08:
            return 0.80
        if min_pf >= 1.12 and min_exp >= 0.03:
            return 0.60
        return 0.45
    if min_pf >= 1.50 and min_exp >= 0.12:
        return 0.45
    if min_pf >= 1.30 and min_exp >= 0.06:
        return 0.35
    if min_pf >= 1.12 and min_exp >= 0.02:
        return 0.25
    return 0.20


def main() -> None:
    study.OUT = OUT
    study.pass_learning = pass_learning
    study.component_candidates = component_candidates
    study.ensemble_candidates = ensemble_candidates
    study.risk_from_stats = risk_from_stats
    study.SWING_SCALE_GRID = tuple(round(0.60 + index * 0.10, 2) for index in range(23))  # .60..2.80
    study.ICT_SCALE_GRID = tuple(round(0.50 + index * 0.10, 2) for index in range(26))  # .50..3.00
    study.main()


if __name__ == "__main__":
    main()
