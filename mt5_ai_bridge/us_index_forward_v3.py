"""US index forward V3: higher-frequency, 1% risk-ceiling research candidate.

V3 reuses the V2 feature/ensemble machinery but locks a more active candidate
family. Candidate selection is still performed only on pre-2021 walk-forward
folds. The 2021+ holdout remains sealed until the final comparison.

Risk is capped at 1.00% of account balance at the initial stop, subject to the
existing 70% allocation/margin cap and downstream drawdown governors.
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
    backtest_window as _v2_backtest_window,
    latest_decision as _v2_latest_decision,
)

CANDIDATES = (
    Candidate("active_fast", 20.0, 0.52, 0.85, 1.75, 2.75, 3, True, 1.00, False),
    Candidate("active_balanced", 25.0, 0.58, 0.75, 2.00, 3.00, 4, True, 1.00, False),
    Candidate("active_trend", 35.0, 0.62, 0.70, 2.25, 3.50, 5, True, 1.10, False),
    Candidate("active_guarded", 40.0, 0.66, 0.65, 2.25, 3.75, 5, True, 1.20, False),
    Candidate("active_open", 30.0, 0.58, 0.78, 2.00, 3.00, 4, False, 1.05, False),
    Candidate("active_long_bias", 30.0, 0.58, 0.75, 2.00, 3.25, 4, True, 1.45, False),
)

LOCKED_CONFIG = USIndexForwardV2Config(
    research_symbol="US500",
    timeframe="D1",
    fast_horizon=3,
    slow_horizon=10,
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
    frequency_reward = 0.05 * min(trades, 30)
    return (
        float(summary["return_pct"])
        + 3.0 * (pfv - 1.0)
        - 0.60 * float(summary["max_drawdown_pct"])
        + frequency_reward
    )


def select_candidate(bars: pd.DataFrame, cfg: USIndexForwardV2Config = LOCKED_CONFIG
                     ) -> tuple[Candidate, dict]:
    folds = (
        ("2016-12-31", "2017-01-01", "2017-12-31"),
        ("2018-12-31", "2019-01-01", "2019-12-31"),
        ("2019-12-31", "2020-01-01", "2020-12-31"),
    )
    report: dict[str, dict] = {}
    for candidate in CANDIDATES:
        fold_results = []
        for train_cutoff, val_start, val_end in folds:
            fold_cfg = replace(cfg, training_cutoff=train_cutoff, min_training_years=5.0)
            val_end_ts = int(pd.Timestamp(val_end, tz="UTC").timestamp())
            # Critical leakage guard: no training or validation trade in this
            # fold can observe a bar after the fold's own validation end date.
            fold_bars = bars[bars["time"].astype(int) <= val_end_ts].copy()
            try:
                artifact = _fit_candidate(fold_bars, candidate, fold_cfg)
                summary = _v2_backtest_window(
                    fold_bars, artifact, fold_cfg, 10_000.0,
                    int(pd.Timestamp(val_start, tz="UTC").timestamp()),
                    val_end_ts,
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
                fold_results.append({"train_cutoff": train_cutoff, "error": str(exc), "score": -999.0})
        valid = [f for f in fold_results if "error" not in f]
        scores = [float(f["score"]) for f in valid]
        positive = sum(1 for f in valid if float(f["return_pct"]) > 0)
        total_trades = sum(int(f["trades"]) for f in valid)
        worst = min(scores) if scores else -999.0
        median = float(np.median(scores)) if scores else -999.0
        robust = median + 0.35 * worst + 0.50 * positive + 0.01 * min(total_trades, 80)
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
        "median fold score + 0.35*worst + 0.50*positive folds + capped activity reward"
    )
    return selected, report


def fit_model(bars: pd.DataFrame, cfg: USIndexForwardV2Config = LOCKED_CONFIG,
              trained_at: Optional[datetime] = None) -> V2Artifact:
    selected, report = select_candidate(bars, cfg)
    artifact = _fit_candidate(bars, selected, cfg, trained_at, report)
    return replace(artifact, version="US_INDEX_FORWARD_V3")


def save_artifact(artifact: V2Artifact, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")


def load_artifact(path: Path) -> V2Artifact:
    artifact = V2Artifact.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    if artifact.version != "US_INDEX_FORWARD_V3":
        raise ValueError("wrong V3 artifact version")
    if artifact.training_years < 5.0:
        raise ValueError("V3 artifact has less than five training years")
    if float(artifact.config.get("risk_percent", 0.0)) > 1.0:
        raise ValueError("V3 artifact exceeds the 1% risk ceiling")
    return artifact


def backtest_window(*args, **kwargs) -> dict:
    summary = _v2_backtest_window(*args, **kwargs)
    summary["model"] = "US_INDEX_FORWARD_V3"
    return summary


def latest_decision(bars: pd.DataFrame, artifact: V2Artifact,
                    cfg: USIndexForwardV2Config = LOCKED_CONFIG):
    return _v2_latest_decision(bars, artifact, cfg)
