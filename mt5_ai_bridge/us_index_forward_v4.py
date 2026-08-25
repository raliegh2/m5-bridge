"""US index forward V4: H4 growth-focused, 1% risk-ceiling research candidate.

V4 keeps the V3 risk ceiling but moves the signal engine from D1 to H4 so the
system can seek more independent opportunities without increasing per-trade stop
risk. Candidate selection remains pre-2021 only. Each walk-forward validation
fold is truncated at its own end before fitting/scoring so later bars cannot leak
through trade exits.

This is a research/demo candidate, not a promise of future profit.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional
import json

import numpy as np
import pandas as pd

from .us_index_forward_v2 import (
    Candidate,
    V2Artifact,
    USIndexForwardV2Config,
    _fit_candidate,
    backtest_window as _base_backtest_window,
    latest_decision as _base_latest_decision,
)

CANDIDATES = (
    Candidate("h4_fast_open", 18.0, 0.54, 0.90, 1.45, 2.25, 6, False, 1.00, False),
    Candidate("h4_fast_guarded", 20.0, 0.58, 0.88, 1.60, 2.50, 7, True, 1.00, False),
    Candidate("h4_balanced", 25.0, 0.62, 0.80, 1.75, 2.80, 8, True, 1.05, False),
    Candidate("h4_trend", 30.0, 0.66, 0.72, 2.00, 3.25, 10, True, 1.15, False),
    Candidate("h4_open_balanced", 22.0, 0.60, 0.82, 1.70, 2.70, 8, False, 1.05, False),
    Candidate("h4_long_bias", 25.0, 0.60, 0.80, 1.70, 2.90, 8, True, 1.35, False),
)

LOCKED_CONFIG = USIndexForwardV2Config(
    research_symbol="US500",
    timeframe="H4",
    fast_horizon=2,
    slow_horizon=6,
    atr_period=14,
    risk_percent=1.00,
    max_fraction_invested=0.70,
    min_training_years=5.0,
    training_cutoff="2020-12-31",
)


def _fold_score(summary: dict) -> float:
    pf = summary["profit_factor"]
    pfv = 3.0 if pf == "inf" else min(3.0, float(pf))
    trades = int(summary["trades"])
    activity = 0.018 * min(trades, 140)
    return (
        float(summary["return_pct"])
        + 3.5 * (pfv - 1.0)
        - 0.70 * float(summary["max_drawdown_pct"])
        + activity
    )


def select_candidate(
    bars: pd.DataFrame,
    cfg: USIndexForwardV2Config = LOCKED_CONFIG,
) -> tuple[Candidate, dict]:
    folds = (
        ("2017-12-31", "2018-01-01", "2018-12-31"),
        ("2018-12-31", "2019-01-01", "2019-12-31"),
        ("2019-12-31", "2020-01-01", "2020-12-31"),
    )
    report: dict[str, dict] = {}
    ordered = bars.sort_values("time").drop_duplicates("time")
    for candidate in CANDIDATES:
        fold_results = []
        for train_cutoff, val_start, val_end in folds:
            end_epoch = int(pd.Timestamp(val_end, tz="UTC").timestamp())
            fold_bars = ordered[ordered["time"] <= end_epoch].copy()
            fold_cfg = replace(cfg, training_cutoff=train_cutoff, min_training_years=5.0)
            try:
                artifact = _fit_candidate(fold_bars, candidate, fold_cfg)
                summary = _base_backtest_window(
                    fold_bars,
                    artifact,
                    fold_cfg,
                    10_000.0,
                    int(pd.Timestamp(val_start, tz="UTC").timestamp()),
                    end_epoch,
                    min_lot=0.1,
                    lot_step=0.1,
                )
                fold_results.append({
                    "train_cutoff": train_cutoff,
                    "validation_year": val_start[:4],
                    "return_pct": summary["return_pct"],
                    "max_drawdown_pct": summary["max_drawdown_pct"],
                    "profit_factor": summary["profit_factor"],
                    "trades": summary["trades"],
                    "score": round(_fold_score(summary), 4),
                })
            except ValueError as exc:
                fold_results.append({
                    "train_cutoff": train_cutoff,
                    "error": str(exc),
                    "score": -999.0,
                })
        valid = [f for f in fold_results if "error" not in f]
        scores = [float(f["score"]) for f in valid]
        positive = sum(1 for f in valid if float(f["return_pct"]) > 0)
        total_trades = sum(int(f["trades"]) for f in valid)
        worst = min(scores) if scores else -999.0
        median = float(np.median(scores)) if scores else -999.0
        robust = median + 0.40 * worst + 0.65 * positive + 0.004 * min(total_trades, 300)
        report[candidate.name] = {
            "folds": fold_results,
            "positive_folds": positive,
            "pre2021_trades": total_trades,
            "median_fold_score": round(median, 4),
            "worst_fold_score": round(worst, 4),
            "robust_selection_score": round(robust, 4),
        }
    selected = max(CANDIDATES, key=lambda c: float(report[c.name]["robust_selection_score"]))
    report["selected"] = selected.name
    report["selection_rule"] = (
        "median + 0.40*worst + 0.65*positive folds + capped H4 activity reward"
    )
    return selected, report


def fit_model(
    bars: pd.DataFrame,
    cfg: USIndexForwardV2Config = LOCKED_CONFIG,
    trained_at: Optional[datetime] = None,
) -> V2Artifact:
    selected, report = select_candidate(bars, cfg)
    artifact = _fit_candidate(bars, selected, cfg, trained_at, report)
    return replace(artifact, version="US_INDEX_FORWARD_V4_H4")


def save_artifact(artifact: V2Artifact, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")


def load_artifact(path: Path) -> V2Artifact:
    artifact = V2Artifact.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    if artifact.version != "US_INDEX_FORWARD_V4_H4":
        raise ValueError("wrong V4 artifact version")
    if artifact.training_years < 5.0:
        raise ValueError("V4 artifact has less than five training years")
    if float(artifact.config.get("risk_percent", 0.0)) > 1.0:
        raise ValueError("V4 artifact exceeds the 1% risk ceiling")
    if str(artifact.config.get("timeframe")) != "H4":
        raise ValueError("V4 artifact is not H4")
    return artifact


def backtest_window(*args, **kwargs) -> dict:
    summary = _base_backtest_window(*args, **kwargs)
    summary["model"] = "US_INDEX_FORWARD_V4_H4"
    return summary


def latest_decision(
    bars: pd.DataFrame,
    artifact: V2Artifact,
    cfg: USIndexForwardV2Config = LOCKED_CONFIG,
):
    return _base_latest_decision(bars, artifact, cfg)
