"""Validated entry point for the V14.18 six-scenario chronological replay.

The replay controller records every parent decision for auditability.  This
entry point normalizes summary attribution so inherited V14.17 shadows are not
misreported as new V14.18 actions.
"""
from __future__ import annotations

import research.v14_18_hierarchical_regime_meta_backtest as study


_original_run = study.HierarchicalRegimeMetaReplay.run


def _attribution_normalized_run(self):
    summary, trades, skipped = _original_run(self)
    counts = {"FULL": 0, "REDUCED": 0, "OBSERVATION": 0, "SHADOW": 0}
    inherited_shadow = 0
    for event in self.v14_18_controller.events:
        parent_risk = float(event.get("v14_18_parent_risk_percent", 0.0) or 0.0)
        final_risk = float(event.get("v14_18_final_risk_percent", 0.0) or 0.0)
        label = str(event.get("v14_18_meta_label", "FULL")).upper()
        if parent_risk <= 0.0:
            inherited_shadow += 1
            continue
        if final_risk < parent_risk - 1e-12:
            counts[label] = counts.get(label, 0) + 1
        else:
            counts["FULL"] += 1

    summary["v14_18_full_labels"] = int(counts["FULL"])
    summary["v14_18_reduced_labels"] = int(counts["REDUCED"])
    summary["v14_18_observation_labels"] = int(counts["OBSERVATION"])
    summary["v14_18_shadow_labels"] = int(counts["SHADOW"])
    summary["v14_18_inherited_shadow_decisions"] = int(inherited_shadow)
    summary["v14_18_active_label_counts"] = counts
    return summary, trades, skipped


study.HierarchicalRegimeMetaReplay.run = _attribution_normalized_run


if __name__ == "__main__":
    study.main()
