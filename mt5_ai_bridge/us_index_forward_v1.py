"""US index forward-test model with a frozen train/validation boundary.

The model fits a small ridge regression on completed daily US500 bars, using
only rows whose forward target also ends on or before the training cutoff. The
learned artifact is then frozen and reused for validation and live forward
testing; the live path never retrains itself.

This is a forward-test candidate, not a validated edge. The runner is demo-only
by default and the sizing layer inherits the current tactical book's risk
philosophy: 0.50% stop risk per trade, 70% maximum notional allocation, a
5%-to-20% drawdown governor, and a hard stop at the governor's hard limit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .risk_v18 import DrawdownGovernor

FEATURES = ("ret_1", "ret_5", "ret_20", "z_20", "vol_20", "atr_pct")
SECONDS_PER_YEAR = 365.2425 * 24 * 60 * 60


@dataclass(frozen=True)
class USIndexForwardConfig:
    research_symbol: str = "US500"
    timeframe: str = "D1"
    horizon_bars: int = 5
    lookback: int = 20
    atr_period: int = 14
    ridge_alpha: float = 10.0
    signal_quantile: float = 0.70
    stop_atr: float = 2.5
    take_profit_atr: float = 3.5
    max_holding_bars: int = 5
    risk_percent: float = 0.50
    max_fraction_invested: float = 0.70
    min_training_years: float = 5.0
    training_cutoff: str = "2020-12-31"

    def validate(self) -> None:
        if self.horizon_bars < 1 or self.max_holding_bars < 1:
            raise ValueError("horizon/max_holding must be positive")
        if self.lookback < 5 or self.atr_period < 2:
            raise ValueError("lookback/atr_period too small")
        if self.ridge_alpha <= 0:
            raise ValueError("ridge_alpha must be positive")
        if not 0.50 <= self.signal_quantile < 1.0:
            raise ValueError("signal_quantile must be in [0.50, 1)")
        if self.stop_atr <= 0 or self.take_profit_atr <= 0:
            raise ValueError("ATR exits must be positive")
        if not 0 < self.risk_percent <= 2.0:
            raise ValueError("risk_percent must be in (0, 2]")
        if not 0 < self.max_fraction_invested <= 1.0:
            raise ValueError("max_fraction_invested must be in (0, 1]")
        if self.min_training_years < 5.0:
            raise ValueError("this model requires at least five training years")


LOCKED_CONFIG = USIndexForwardConfig()


@dataclass(frozen=True)
class TrainedArtifact:
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
    coefficients: tuple[float, ...]
    intercept: float
    signal_threshold: float
    config: dict

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["feature_names"] = list(self.feature_names)
        payload["feature_mean"] = list(self.feature_mean)
        payload["feature_std"] = list(self.feature_std)
        payload["coefficients"] = list(self.coefficients)
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "TrainedArtifact":
        return cls(
            version=str(payload["version"]),
            research_symbol=str(payload["research_symbol"]),
            trained_at_utc=str(payload["trained_at_utc"]),
            training_start=int(payload["training_start"]),
            training_end=int(payload["training_end"]),
            training_years=float(payload["training_years"]),
            training_rows=int(payload["training_rows"]),
            feature_names=tuple(payload["feature_names"]),
            feature_mean=tuple(float(x) for x in payload["feature_mean"]),
            feature_std=tuple(float(x) for x in payload["feature_std"]),
            coefficients=tuple(float(x) for x in payload["coefficients"]),
            intercept=float(payload["intercept"]),
            signal_threshold=float(payload["signal_threshold"]),
            config=dict(payload["config"]),
        )


def save_artifact(artifact: TrainedArtifact, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")


def load_artifact(path: Path) -> TrainedArtifact:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    artifact = TrainedArtifact.from_dict(payload)
    if tuple(artifact.feature_names) != FEATURES:
        raise ValueError("artifact feature schema does not match this code")
    if artifact.training_years < 5.0:
        raise ValueError("artifact was not trained on at least five years")
    return artifact


def _atr(frame: pd.DataFrame, period: int) -> pd.Series:
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean()


def feature_frame(bars: pd.DataFrame, cfg: USIndexForwardConfig = LOCKED_CONFIG
                  ) -> pd.DataFrame:
    """Build backward-looking features and a future-return training target."""
    cfg.validate()
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars missing columns: {sorted(missing)}")

    df = bars.sort_values("time").drop_duplicates("time").copy()
    close = df["close"].astype(float)
    ret = close.pct_change()
    mean20 = close.rolling(cfg.lookback).mean()
    sd20 = close.rolling(cfg.lookback).std(ddof=0)

    df["ret_1"] = ret
    df["ret_5"] = close.pct_change(5)
    df["ret_20"] = close.pct_change(20)
    df["z_20"] = (close - mean20) / sd20.replace(0.0, np.nan)
    df["vol_20"] = ret.rolling(20).std(ddof=0)
    df["atr"] = _atr(df, cfg.atr_period)
    df["atr_pct"] = df["atr"] / close.replace(0.0, np.nan)
    df["target"] = close.shift(-cfg.horizon_bars) / close - 1.0
    df["target_time"] = df["time"].shift(-cfg.horizon_bars)
    return df


def _coverage_years(start: int, end: int) -> float:
    if end <= start:
        return 0.0
    return (end - start) / SECONDS_PER_YEAR


def fit_model(bars: pd.DataFrame, cfg: USIndexForwardConfig = LOCKED_CONFIG,
              trained_at: Optional[datetime] = None) -> TrainedArtifact:
    """Fit only on targets fully resolved by ``training_cutoff``."""
    cfg.validate()
    df = feature_frame(bars, cfg)
    cutoff = int(pd.Timestamp(cfg.training_cutoff, tz="UTC").timestamp())
    train = df[(df["time"] <= cutoff) & (df["target_time"] <= cutoff)].dropna(
        subset=list(FEATURES) + ["target"]
    )
    if train.empty:
        raise ValueError("no eligible training rows before training cutoff")

    start = int(train["time"].iloc[0])
    end = int(train["target_time"].iloc[-1])
    years = _coverage_years(start, end)
    if years < cfg.min_training_years:
        raise ValueError(
            f"training span is {years:.2f} years; need {cfg.min_training_years:.2f}+"
        )

    x = train.loc[:, FEATURES].to_numpy(dtype=float)
    y = train["target"].to_numpy(dtype=float)
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std > 1e-12, std, 1.0)
    z = (x - mean) / std

    design = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(design.shape[1]) * cfg.ridge_alpha
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    train_pred = design @ beta
    threshold = float(np.quantile(np.abs(train_pred), cfg.signal_quantile))
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("training produced a non-positive signal threshold")

    now = trained_at or datetime.now(timezone.utc)
    return TrainedArtifact(
        version="US_INDEX_FORWARD_V1",
        research_symbol=cfg.research_symbol,
        trained_at_utc=now.isoformat(),
        training_start=start,
        training_end=end,
        training_years=round(years, 4),
        training_rows=len(train),
        feature_names=FEATURES,
        feature_mean=tuple(float(v) for v in mean),
        feature_std=tuple(float(v) for v in std),
        coefficients=tuple(float(v) for v in beta[1:]),
        intercept=float(beta[0]),
        signal_threshold=threshold,
        config=asdict(cfg),
    )


def predict_rows(features: pd.DataFrame, artifact: TrainedArtifact) -> np.ndarray:
    x = features.loc[:, artifact.feature_names].to_numpy(dtype=float)
    mean = np.asarray(artifact.feature_mean, dtype=float)
    std = np.asarray(artifact.feature_std, dtype=float)
    coef = np.asarray(artifact.coefficients, dtype=float)
    z = (x - mean) / std
    return artifact.intercept + z @ coef


@dataclass(frozen=True)
class ModelDecision:
    side: str
    prediction: float
    threshold: float
    atr: float
    close: float
    time: int


def latest_decision(bars: pd.DataFrame, artifact: TrainedArtifact,
                    cfg: USIndexForwardConfig = LOCKED_CONFIG) -> ModelDecision:
    df = feature_frame(bars, cfg).dropna(subset=list(FEATURES) + ["atr"])
    if df.empty:
        raise ValueError("not enough completed bars for a signal")
    row = df.iloc[-1:]
    pred = float(predict_rows(row, artifact)[0])
    side = "FLAT"
    if pred >= artifact.signal_threshold:
        side = "BUY"
    elif pred <= -artifact.signal_threshold:
        side = "SELL"
    r = row.iloc[0]
    return ModelDecision(
        side=side,
        prediction=pred,
        threshold=artifact.signal_threshold,
        atr=float(r["atr"]),
        close=float(r["close"]),
        time=int(r["time"]),
    )


def size_for_risk(balance: float, price: float, stop_distance: float,
                  risk_percent: float, max_fraction_invested: float,
                  governor_multiplier: float = 1.0,
                  min_lot: float = 0.1, lot_step: float = 0.1) -> float:
    """Risk-based size capped by the tactical model's max invested fraction."""
    if min(balance, price, stop_distance, governor_multiplier) <= 0:
        return 0.0
    risk_budget = balance * (risk_percent / 100.0) * governor_multiplier
    by_stop = risk_budget / stop_distance
    by_notional = (balance * max_fraction_invested * governor_multiplier) / price
    raw = min(by_stop, by_notional)
    if raw < min_lot:
        return 0.0
    steps = int(raw / lot_step)
    return round(steps * lot_step, 8)


def _max_drawdown(curve: Iterable[float]) -> float:
    values = np.asarray(list(curve), dtype=float)
    if values.size == 0:
        return 0.0
    peak = np.maximum.accumulate(values)
    return float(np.max((peak - values) / peak))


def backtest_frozen(bars: pd.DataFrame, artifact: TrainedArtifact,
                    cfg: USIndexForwardConfig = LOCKED_CONFIG,
                    starting_balance: float = 10_000.0,
                    min_lot: float = 0.1, lot_step: float = 0.1) -> dict:
    """Evaluate only rows strictly after the training cutoff without refitting."""
    df = feature_frame(bars, cfg).dropna(subset=list(FEATURES) + ["atr"])
    df = df.reset_index(drop=True)
    cutoff = int(pd.Timestamp(cfg.training_cutoff, tz="UTC").timestamp())
    validation_idx = [
        i for i in range(len(df) - 1) if int(df.loc[i, "time"]) > cutoff
    ]
    if not validation_idx:
        raise ValueError("no post-training rows available for validation")

    balance = float(starting_balance)
    equity_curve = [balance]
    governor = DrawdownGovernor(soft_limit=0.05, hard_limit=0.20, floor=0.25)
    trades = []
    i = validation_idx[0]
    last_i = validation_idx[-1]

    while i <= last_i and i < len(df) - 1:
        row = df.iloc[i:i + 1]
        pred = float(predict_rows(row, artifact)[0])
        side = (
            1 if pred >= artifact.signal_threshold
            else -1 if pred <= -artifact.signal_threshold
            else 0
        )
        if side == 0:
            i += 1
            continue

        signal = df.iloc[i]
        entry_bar = df.iloc[i + 1]
        entry = float(entry_bar["open"])
        atr = float(signal["atr"])
        if not np.isfinite(entry) or not np.isfinite(atr) or entry <= 0 or atr <= 0:
            i += 1
            continue

        governor.observe(balance)
        multiplier = governor.multiplier(balance)
        if multiplier <= 0:
            break
        stop_distance = cfg.stop_atr * atr
        lots = size_for_risk(
            balance, entry, stop_distance, cfg.risk_percent,
            cfg.max_fraction_invested, multiplier, min_lot, lot_step,
        )
        if lots <= 0:
            i += 1
            continue

        stop = entry - stop_distance if side > 0 else entry + stop_distance
        target_distance = cfg.take_profit_atr * atr
        target = entry + target_distance if side > 0 else entry - target_distance
        exit_price = float(entry_bar["close"])
        exit_reason = "TIME"
        exit_i = i + 1

        max_j = min(i + cfg.max_holding_bars, len(df) - 1)
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
        equity_curve.append(balance)
        trades.append({
            "signal_time": int(signal["time"]),
            "entry_time": int(entry_bar["time"]),
            "exit_time": int(df.iloc[exit_i]["time"]),
            "side": "BUY" if side > 0 else "SELL",
            "prediction": pred,
            "entry": entry,
            "exit": exit_price,
            "lots": lots,
            "profit": round(pnl, 2),
            "reason": exit_reason,
        })
        i = max(i + 1, exit_i + 1)

    profits = [t["profit"] for t in trades]
    wins = [p for p in profits if p > 0]
    losses = [-p for p in profits if p < 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)
    pf = (
        gross_win / gross_loss if gross_loss > 0
        else float("inf") if gross_win > 0
        else 0.0
    )
    return {
        "model": "US_INDEX_FORWARD_V1",
        "research_symbol": cfg.research_symbol,
        "training_start": artifact.training_start,
        "training_end": artifact.training_end,
        "training_years": artifact.training_years,
        "validation_start": int(df.loc[validation_idx[0], "time"]),
        "validation_end": int(df.loc[validation_idx[-1], "time"]),
        "starting_balance": starting_balance,
        "final_balance": round(balance, 2),
        "return_pct": round((balance / starting_balance - 1.0) * 100.0, 3),
        "max_drawdown_pct": round(_max_drawdown(equity_curve) * 100.0, 3),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "profit_factor": round(float(pf), 4) if np.isfinite(pf) else "inf",
        "signal_threshold": artifact.signal_threshold,
        "trade_log": trades,
    }
