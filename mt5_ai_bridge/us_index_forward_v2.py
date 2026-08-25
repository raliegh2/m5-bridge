"""US index forward V2: pre-cutoff walk-forward selected multi-horizon ensemble.

V2 deliberately keeps the 2021+ validation window sealed. Model architecture and
candidate selection use only data whose labels resolve on or before the selected
training cutoff. A small locked candidate family is scored on pre-2021 walk-forward
folds, then the winning specification is refit through 2020-12-31 and frozen.

The model is a research/demo forward-test candidate, not an assurance of profit.
Risk remains aligned with V1: 0.50% stop risk, 70% maximum allocation/margin budget,
and downstream demo-only execution guards.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .risk_v18 import DrawdownGovernor
from .us_index_forward_v1 import size_for_risk

FEATURES = (
    "ret_1", "ret_3", "ret_5", "ret_10", "ret_20", "ret_60",
    "z_10", "z_20", "z_60",
    "vol_10", "vol_20", "vol_60", "down_vol_20",
    "atr_pct", "range_pos_20", "drawdown_60", "gap_1",
)
SECONDS_PER_YEAR = 365.2425 * 24 * 60 * 60


@dataclass(frozen=True)
class Candidate:
    name: str
    ridge_alpha: float
    signal_quantile: float
    fast_weight: float
    stop_atr: float
    take_profit_atr: float
    max_holding_bars: int
    regime_filter: bool = True
    short_multiplier: float = 1.0
    long_only: bool = False


CANDIDATES = (
    Candidate("balanced", 20.0, 0.72, 0.60, 2.25, 3.50, 6, True, 1.00, False),
    Candidate("selective", 30.0, 0.80, 0.60, 2.25, 3.75, 7, True, 1.15, False),
    Candidate("trend_guard", 40.0, 0.74, 0.50, 2.50, 4.00, 8, True, 1.30, False),
    Candidate("long_bias", 30.0, 0.76, 0.55, 2.25, 3.75, 7, True, 1.55, False),
    Candidate("long_only", 30.0, 0.76, 0.55, 2.25, 3.75, 7, True, 99.0, True),
)


@dataclass(frozen=True)
class USIndexForwardV2Config:
    research_symbol: str = "US500"
    timeframe: str = "D1"
    fast_horizon: int = 3
    slow_horizon: int = 10
    atr_period: int = 14
    risk_percent: float = 0.50
    max_fraction_invested: float = 0.70
    min_training_years: float = 5.0
    training_cutoff: str = "2020-12-31"

    def validate(self) -> None:
        if self.fast_horizon < 1 or self.slow_horizon <= self.fast_horizon:
            raise ValueError("invalid fast/slow horizons")
        if self.atr_period < 2:
            raise ValueError("atr_period too small")
        if not 0 < self.risk_percent <= 2.0:
            raise ValueError("risk_percent must be in (0, 2]")
        if not 0 < self.max_fraction_invested <= 1.0:
            raise ValueError("max_fraction_invested must be in (0, 1]")
        if self.min_training_years < 5.0:
            raise ValueError("V2 requires at least five training years")


LOCKED_CONFIG = USIndexForwardV2Config()


@dataclass(frozen=True)
class V2Artifact:
    version: str
    research_symbol: str
    trained_at_utc: str
    training_start: int
    training_end: int
    training_years: float
    training_rows: int
    feature_names: tuple[str, ...]
    feature_mean: tuple[float, ...]
    feature_std: tuple[float, ...]
    fast_coefficients: tuple[float, ...]
    fast_intercept: float
    slow_coefficients: tuple[float, ...]
    slow_intercept: float
    score_threshold: float
    candidate: dict
    selection_report: dict
    config: dict

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in (
            "feature_names", "feature_mean", "feature_std",
            "fast_coefficients", "slow_coefficients",
        ):
            d[k] = list(d[k])
        return d

    @classmethod
    def from_dict(cls, p: dict) -> "V2Artifact":
        return cls(
            version=str(p["version"]),
            research_symbol=str(p["research_symbol"]),
            trained_at_utc=str(p["trained_at_utc"]),
            training_start=int(p["training_start"]),
            training_end=int(p["training_end"]),
            training_years=float(p["training_years"]),
            training_rows=int(p["training_rows"]),
            feature_names=tuple(p["feature_names"]),
            feature_mean=tuple(float(x) for x in p["feature_mean"]),
            feature_std=tuple(float(x) for x in p["feature_std"]),
            fast_coefficients=tuple(float(x) for x in p["fast_coefficients"]),
            fast_intercept=float(p["fast_intercept"]),
            slow_coefficients=tuple(float(x) for x in p["slow_coefficients"]),
            slow_intercept=float(p["slow_intercept"]),
            score_threshold=float(p["score_threshold"]),
            candidate=dict(p["candidate"]),
            selection_report=dict(p.get("selection_report", {})),
            config=dict(p["config"]),
        )


def save_artifact(artifact: V2Artifact, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")


def load_artifact(path: Path) -> V2Artifact:
    artifact = V2Artifact.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    if artifact.version != "US_INDEX_FORWARD_V2":
        raise ValueError("wrong artifact version")
    if artifact.feature_names != FEATURES:
        raise ValueError("V2 feature schema mismatch")
    if artifact.training_years < 5.0:
        raise ValueError("V2 artifact has less than five training years")
    return artifact


def _coverage_years(start: int, end: int) -> float:
    return max(0.0, (end - start) / SECONDS_PER_YEAR)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def feature_frame(bars: pd.DataFrame, cfg: USIndexForwardV2Config = LOCKED_CONFIG
                  ) -> pd.DataFrame:
    cfg.validate()
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars missing columns: {sorted(missing)}")
    df = bars.sort_values("time").drop_duplicates("time").copy()
    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    ret = close.pct_change()
    for n in (1, 3, 5, 10, 20, 60):
        df[f"ret_{n}"] = close.pct_change(n)
    for n in (10, 20, 60):
        mean = close.rolling(n).mean()
        sd = close.rolling(n).std(ddof=0).replace(0.0, np.nan)
        df[f"z_{n}"] = (close - mean) / sd
        df[f"vol_{n}"] = ret.rolling(n).std(ddof=0)
    downside = ret.where(ret < 0.0, 0.0)
    df["down_vol_20"] = downside.rolling(20).std(ddof=0)
    df["atr"] = _atr(df, cfg.atr_period)
    df["atr_pct"] = df["atr"] / close.replace(0.0, np.nan)
    hi20 = df["high"].rolling(20).max()
    lo20 = df["low"].rolling(20).min()
    df["range_pos_20"] = (close - lo20) / (hi20 - lo20).replace(0.0, np.nan)
    hi60 = df["high"].rolling(60).max()
    df["drawdown_60"] = close / hi60.replace(0.0, np.nan) - 1.0
    df["gap_1"] = open_ / close.shift(1).replace(0.0, np.nan) - 1.0
    df["target_fast"] = close.shift(-cfg.fast_horizon) / close - 1.0
    df["target_slow"] = close.shift(-cfg.slow_horizon) / close - 1.0
    df["target_fast_time"] = df["time"].shift(-cfg.fast_horizon)
    df["target_slow_time"] = df["time"].shift(-cfg.slow_horizon)
    return df


def _ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> tuple[float, np.ndarray]:
    design = np.column_stack([np.ones(len(x)), x])
    penalty = np.eye(design.shape[1]) * float(alpha)
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return float(beta[0]), beta[1:]


def _fit_candidate(bars: pd.DataFrame, candidate: Candidate,
                   cfg: USIndexForwardV2Config, trained_at: Optional[datetime] = None,
                   selection_report: Optional[dict] = None) -> V2Artifact:
    df = feature_frame(bars, cfg)
    cutoff = int(pd.Timestamp(cfg.training_cutoff, tz="UTC").timestamp())
    train = df[
        (df["time"] <= cutoff)
        & (df["target_fast_time"] <= cutoff)
        & (df["target_slow_time"] <= cutoff)
    ].dropna(subset=list(FEATURES) + ["target_fast", "target_slow"])
    if train.empty:
        raise ValueError("no eligible V2 training rows")
    start = int(train["time"].iloc[0])
    end = int(train["target_slow_time"].iloc[-1])
    years = _coverage_years(start, end)
    if years < cfg.min_training_years:
        raise ValueError(f"training span is {years:.2f} years; need {cfg.min_training_years:.2f}+")

    x = train.loc[:, FEATURES].to_numpy(dtype=float)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std > 1e-12, std, 1.0)
    z = (x - mean) / std
    fast_intercept, fast_coef = _ridge(z, train["target_fast"].to_numpy(float), candidate.ridge_alpha)
    slow_intercept, slow_coef = _ridge(z, train["target_slow"].to_numpy(float), candidate.ridge_alpha)
    fast_pred = fast_intercept + z @ fast_coef
    slow_pred = slow_intercept + z @ slow_coef
    score = candidate.fast_weight * fast_pred + (1.0 - candidate.fast_weight) * slow_pred
    threshold = float(np.quantile(np.abs(score), candidate.signal_quantile))
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("V2 produced invalid score threshold")
    now = trained_at or datetime.now(timezone.utc)
    return V2Artifact(
        version="US_INDEX_FORWARD_V2",
        research_symbol=cfg.research_symbol,
        trained_at_utc=now.isoformat(),
        training_start=start,
        training_end=end,
        training_years=round(years, 4),
        training_rows=len(train),
        feature_names=FEATURES,
        feature_mean=tuple(float(v) for v in mean),
        feature_std=tuple(float(v) for v in std),
        fast_coefficients=tuple(float(v) for v in fast_coef),
        fast_intercept=fast_intercept,
        slow_coefficients=tuple(float(v) for v in slow_coef),
        slow_intercept=slow_intercept,
        score_threshold=threshold,
        candidate=asdict(candidate),
        selection_report=selection_report or {},
        config=asdict(cfg),
    )


def predict_rows(features: pd.DataFrame, artifact: V2Artifact) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = features.loc[:, artifact.feature_names].to_numpy(float)
    z = (x - np.asarray(artifact.feature_mean)) / np.asarray(artifact.feature_std)
    fast = artifact.fast_intercept + z @ np.asarray(artifact.fast_coefficients)
    slow = artifact.slow_intercept + z @ np.asarray(artifact.slow_coefficients)
    w = float(artifact.candidate["fast_weight"])
    return fast, slow, w * fast + (1.0 - w) * slow


def _side(row: pd.Series, score: float, artifact: V2Artifact) -> int:
    c = artifact.candidate
    threshold = float(artifact.score_threshold)
    long_ok = score >= threshold
    short_ok = score <= -threshold * float(c.get("short_multiplier", 1.0))
    if bool(c.get("regime_filter", True)):
        # Longs require the broad regime not to be severely broken. Shorts are
        # allowed only when both medium and long horizons are already negative.
        long_ok = long_ok and float(row["ret_60"]) > -0.08 and float(row["drawdown_60"]) > -0.12
        short_ok = short_ok and float(row["ret_20"]) < 0.0 and float(row["ret_60"]) < 0.0
    if bool(c.get("long_only", False)):
        short_ok = False
    return 1 if long_ok else -1 if short_ok else 0


def _max_drawdown(curve: Iterable[float]) -> float:
    values = np.asarray(list(curve), dtype=float)
    if values.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(values)
    return float(np.max((peaks - values) / peaks))


def backtest_window(bars: pd.DataFrame, artifact: V2Artifact,
                    cfg: USIndexForwardV2Config = LOCKED_CONFIG,
                    starting_balance: float = 10_000.0,
                    start_time: Optional[int] = None,
                    end_time: Optional[int] = None,
                    min_lot: float = 0.1, lot_step: float = 0.1) -> dict:
    df = feature_frame(bars, cfg).dropna(subset=list(FEATURES) + ["atr"]).reset_index(drop=True)
    cutoff = int(pd.Timestamp(cfg.training_cutoff, tz="UTC").timestamp())
    start_time = int(start_time) if start_time is not None else cutoff + 1
    eligible = [
        i for i in range(len(df) - 1)
        if int(df.loc[i, "time"]) >= start_time
        and (end_time is None or int(df.loc[i, "time"]) <= int(end_time))
    ]
    if not eligible:
        raise ValueError("no rows in requested V2 backtest window")
    balance = float(starting_balance)
    curve = [balance]
    snapshots = [{"time": int(df.loc[eligible[0], "time"]), "balance": balance}]
    governor = DrawdownGovernor(soft_limit=0.05, hard_limit=0.20, floor=0.25)
    trades: list[dict] = []
    i, last_i = eligible[0], eligible[-1]
    c = artifact.candidate

    while i <= last_i and i < len(df) - 1:
        row = df.iloc[i:i+1]
        fast, slow, scores = predict_rows(row, artifact)
        score = float(scores[0])
        signal = df.iloc[i]
        side = _side(signal, score, artifact)
        if side == 0:
            i += 1
            continue
        entry_bar = df.iloc[i + 1]
        entry = float(entry_bar["open"])
        atr = float(signal["atr"])
        if not np.isfinite(entry) or not np.isfinite(atr) or entry <= 0 or atr <= 0:
            i += 1
            continue
        governor.observe(balance)
        mult = governor.multiplier(balance)
        if mult <= 0:
            break
        stop_distance = float(c["stop_atr"]) * atr
        lots = size_for_risk(
            balance, entry, stop_distance, cfg.risk_percent,
            cfg.max_fraction_invested, mult, min_lot, lot_step,
        )
        if lots <= 0:
            i += 1
            continue
        stop = entry - stop_distance if side > 0 else entry + stop_distance
        target_distance = float(c["take_profit_atr"]) * atr
        target = entry + target_distance if side > 0 else entry - target_distance
        exit_price = float(entry_bar["close"])
        exit_reason, exit_i = "TIME", i + 1
        max_j = min(i + int(c["max_holding_bars"]), last_i + 1, len(df) - 1)
        for j in range(i + 1, max_j + 1):
            bar = df.iloc[j]
            if side > 0:
                if float(bar["low"]) <= stop:
                    exit_price, exit_reason, exit_i = stop, "STOP", j
                    break
                if float(bar["high"]) >= target:
                    exit_price, exit_reason, exit_i = target, "TARGET", j
                    break
            else:
                if float(bar["high"]) >= stop:
                    exit_price, exit_reason, exit_i = stop, "STOP", j
                    break
                if float(bar["low"]) <= target:
                    exit_price, exit_reason, exit_i = target, "TARGET", j
                    break
            exit_price, exit_i = float(bar["close"]), j
        pnl = (exit_price - entry) * side * lots
        balance += pnl
        curve.append(balance)
        exit_time = int(df.iloc[exit_i]["time"])
        snapshots.append({"time": exit_time, "balance": balance})
        trades.append({
            "signal_time": int(signal["time"]),
            "entry_time": int(entry_bar["time"]),
            "exit_time": exit_time,
            "side": "BUY" if side > 0 else "SELL",
            "fast_prediction": float(fast[0]),
            "slow_prediction": float(slow[0]),
            "score": score,
            "entry": entry,
            "exit": exit_price,
            "lots": lots,
            "profit": round(pnl, 2),
            "reason": exit_reason,
        })
        i = max(i + 1, exit_i + 1)

    profits = [float(t["profit"]) for t in trades]
    wins = [p for p in profits if p > 0]
    losses = [-p for p in profits if p < 0]
    gross_win, gross_loss = sum(wins), sum(losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    return {
        "model": "US_INDEX_FORWARD_V2",
        "candidate": c["name"],
        "starting_balance": starting_balance,
        "final_balance": round(balance, 2),
        "return_pct": round((balance / starting_balance - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(_max_drawdown(curve) * 100.0, 4),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "profit_factor": round(float(pf), 4) if np.isfinite(pf) else "inf",
        "trade_log": trades,
        "equity_snapshots": snapshots,
    }


def _fold_score(summary: dict) -> float:
    pf = summary["profit_factor"]
    pfv = 3.0 if pf == "inf" else min(3.0, float(pf))
    # Reward return and PF, penalize drawdown. This is intentionally simple and
    # fixed before the 2021+ holdout is inspected.
    return float(summary["return_pct"]) + 3.0 * (pfv - 1.0) - 0.60 * float(summary["max_drawdown_pct"])


def select_candidate(bars: pd.DataFrame, cfg: USIndexForwardV2Config = LOCKED_CONFIG) -> tuple[Candidate, dict]:
    """Choose from the locked candidate family using pre-2021 walk-forward folds."""
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
            try:
                artifact = _fit_candidate(bars, candidate, fold_cfg)
                summary = backtest_window(
                    bars, artifact, fold_cfg, 10_000.0,
                    int(pd.Timestamp(val_start, tz="UTC").timestamp()),
                    int(pd.Timestamp(val_end, tz="UTC").timestamp()),
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
        worst = min(scores) if scores else -999.0
        median = float(np.median(scores)) if scores else -999.0
        robust = median + 0.35 * worst + 0.50 * positive
        report[candidate.name] = {
            "folds": fold_results,
            "positive_folds": positive,
            "median_fold_score": round(median, 4),
            "worst_fold_score": round(worst, 4),
            "robust_selection_score": round(robust, 4),
        }
    selected = max(CANDIDATES, key=lambda c: float(report[c.name]["robust_selection_score"]))
    report["selected"] = selected.name
    report["selection_rule"] = "median fold score + 0.35*worst fold score + 0.50*positive folds"
    return selected, report


def fit_model(bars: pd.DataFrame, cfg: USIndexForwardV2Config = LOCKED_CONFIG,
              trained_at: Optional[datetime] = None) -> V2Artifact:
    selected, report = select_candidate(bars, cfg)
    return _fit_candidate(bars, selected, cfg, trained_at, report)


@dataclass(frozen=True)
class ModelDecision:
    side: str
    fast_prediction: float
    slow_prediction: float
    score: float
    threshold: float
    atr: float
    close: float
    time: int


def latest_decision(bars: pd.DataFrame, artifact: V2Artifact,
                    cfg: USIndexForwardV2Config = LOCKED_CONFIG) -> ModelDecision:
    df = feature_frame(bars, cfg).dropna(subset=list(FEATURES) + ["atr"])
    if df.empty:
        raise ValueError("not enough completed bars for V2 signal")
    row = df.iloc[-1:]
    fast, slow, score = predict_rows(row, artifact)
    r = row.iloc[0]
    side_num = _side(r, float(score[0]), artifact)
    return ModelDecision(
        side="BUY" if side_num > 0 else "SELL" if side_num < 0 else "FLAT",
        fast_prediction=float(fast[0]),
        slow_prediction=float(slow[0]),
        score=float(score[0]),
        threshold=float(artifact.score_threshold),
        atr=float(r["atr"]),
        close=float(r["close"]),
        time=int(r["time"]),
    )
