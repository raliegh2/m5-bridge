"""Entrypoint that normalizes incumbent sleeve ranking before V14.10 replay."""
from __future__ import annotations

from research import v14_10_multi_engine_fxcm_backtest as study

_original_build_v149_candidates = study.build_v149_candidates


def _build_v149_candidates_with_rank(source):
    frame, evidence = _original_build_v149_candidates(source)
    frame = frame.copy()
    frame["selection_score"] = 0.0
    return frame, evidence


study.build_v149_candidates = _build_v149_candidates_with_rank


if __name__ == "__main__":
    study.main()
