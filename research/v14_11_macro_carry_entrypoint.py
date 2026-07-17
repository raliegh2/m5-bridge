"""Deterministic entrypoint for the V14.11 macro-carry research replay.

The main study expects all candidate streams to contain walk-forward gate
metadata before portfolio admission. This wrapper applies the unchanged V14.9
365-day gate to the incumbent SWING and ICT sleeves before invoking the V14.11
study. Strategy definitions, evidence gates, risk tiers, and the 2022-2026
post-selection boundary are not changed.
"""
from __future__ import annotations

from research import v14_11_macro_carry_fxcm as study

_original_build_v149_candidates = study.build_v149_candidates


def _build_gated_v149_candidates(source):
    frame, evidence = _original_build_v149_candidates(source)
    gated = study.v149.apply_walk_forward_gate(frame)
    gated["selection_score"] = 0.0
    return gated, evidence


study.build_v149_candidates = _build_gated_v149_candidates


if __name__ == "__main__":
    study.main()
