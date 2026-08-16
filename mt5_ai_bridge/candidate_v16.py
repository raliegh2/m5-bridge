"""V16: a locked mean-reversion candidate, pre-registered against V15.

V15 assumed trend persistence and was refuted, because the persistence is not
there: on audited post-inception H4 data, every variance ratio sits at or below
1.0 (AUDUSD 0.909, GBPUSD 0.910, GBPJPY 0.896 at q=120). That same measurement
points weakly the other way, and this candidate tests that direction.

The rule: when price stretches ``entry_z`` standard deviations from its rolling
mean, enter *against* the move; exit on reversion to ``exit_z``, on a stop at
``stop_z``, or when ``max_holding_bars`` elapses.

Three disciplines carried over deliberately:

* **Parameters are textbook, not searched.** 2-sigma is the conventional
  definition of a stretched move; the 20-bar lookback is reused unchanged from
  V15 rather than re-optimised, so this is one new trial and not a family.
* **A time stop is mandatory.** Mean reversion without one silently becomes
  buy-and-hold in a trending market, which is how a reversion strategy hides an
  unbounded loss.
* **The expected outcome is failure.** No variance ratio was individually
  significant. This is one honest test of a weak signal, and it is recorded as
  such in ``research/v16_locked_candidate.json``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from .candidate_v15 import CandidateResult, CandidateTrade, _atr
from .costs import ZERO_COST, CostModel
from .enums import Signal
from .instruments import Instrument, settle

__all__ = ["ReversionConfig", "LOCKED_V16", "locked_config_v16",
           "add_bands", "replay_v16"]

LOCK_PATH = Path(__file__).resolve().parents[1] / "research" / "v16_locked_candidate.json"


@dataclass(frozen=True)
class ReversionConfig:
    timeframe_minutes: int = 240
    lookback: int = 20
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    atr_period: int = 14
    max_holding_bars: int = 60
    min_atr_pips: float = 8.0
    risk_percent: float = 0.5
    pip: float = 0.0001
    contract_size: float = 100_000.0

    def validate(self) -> None:
        if self.lookback < 2:
            raise ValueError("lookback must be at least 2")
        if not 0 <= self.exit_z < self.entry_z:
            raise ValueError("exit_z must be below entry_z and non-negative")
        if self.stop_z <= self.entry_z:
            raise ValueError("stop_z must exceed entry_z")
        if self.max_holding_bars < 1:
            raise ValueError(
                "max_holding_bars must be positive; without a time stop a "
                "reversion trade becomes an unbounded trend position")
        if self.pip <= 0 or self.contract_size <= 0:
            raise ValueError("pip and contract_size must be positive")
        if self.atr_period < 2:
            raise ValueError("atr_period must be at least 2")


LOCKED_V16 = ReversionConfig()


def locked_config_v16(path: Optional[Path] = None) -> ReversionConfig:
    """Load the frozen parameters, refusing a lock file edited after the fact."""
    path = Path(path or LOCK_PATH)
    if not path.exists():
        raise FileNotFoundError(f"lock file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg = ReversionConfig(**payload.get("parameters", {}))
    cfg.validate()
    if cfg != LOCKED_V16:
        raise ValueError(
            f"lock file {path} disagrees with the code's LOCKED_V16 config.\n"
            f"  file: {asdict(cfg)}\n  code: {asdict(LOCKED_V16)}")
    return cfg


def add_bands(df: pd.DataFrame, cfg: ReversionConfig) -> pd.DataFrame:
    """Attach the rolling mean, standard deviation, z-score and ATR.

    Every column is shifted by one bar, so a bar's signal uses only bars
    strictly before it.
    """
    out = df.copy()
    close = out["close"]
    mean = close.rolling(cfg.lookback).mean()
    sd = close.rolling(cfg.lookback).std(ddof=0)
    out["mean"] = mean.shift(1)
    out["sd"] = sd.shift(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["z"] = (close.shift(1) - out["mean"]) / out["sd"].replace(0, np.nan)
    out["atr"] = _atr(out, cfg.atr_period).shift(1)
    return out


def replay_v16(bars: pd.DataFrame, cfg: ReversionConfig = LOCKED_V16,
               cost: CostModel = ZERO_COST,
               starting_balance: float = 10_000.0,
               instrument=None) -> CandidateResult:
    """Replay the locked reversion rules. One position at a time."""
    cfg.validate()
    if instrument is None:
        instrument = Instrument("CONFIG", cfg.pip, cfg.contract_size)
    else:
        cfg = replace(cfg, pip=instrument.pip,
                      contract_size=instrument.contract_size)
        cfg.validate()

    df = add_bands(bars, cfg)
    balance = starting_balance
    trades: List[CandidateTrade] = []
    pip_value_per_lot = cfg.pip * cfg.contract_size

    side: Optional[Signal] = None
    entry = stop = 0.0
    lots = 0.0
    entry_time = 0
    bars_held = 0
    last_row = None

    def close_out(exit_price: float, when: int, reason: str) -> None:
        nonlocal balance, side
        direction = 1.0 if side is Signal.BUY else -1.0
        nights = max(0, int((when - entry_time) // 86_400))
        gross, trade_cost = settle(instrument, side, lots, entry,
                                   float(exit_price), nights, cost, when)
        profit = gross - trade_cost
        balance += profit
        trades.append(CandidateTrade(
            entry_time=entry_time, exit_time=when, side=side, entry=entry,
            exit=float(exit_price), lots=lots,
            pips=round(direction * (exit_price - entry) / cfg.pip, 1),
            profit=round(profit, 2), cost=round(trade_cost, 2), reason=reason))
        side = None

    for row in df.itertuples(index=False):
        last_row = row
        z = getattr(row, "z")
        atr = getattr(row, "atr")
        if not np.isfinite(atr) or atr <= 0 or not np.isfinite(z):
            continue

        # --- manage an open position ---
        if side is not None:
            bars_held += 1
            exit_price = reason = None
            if side is Signal.BUY:
                if row.low <= stop:
                    exit_price, reason = stop, "STOP"
                elif z >= -cfg.exit_z:
                    exit_price, reason = float(row.close), "REVERTED"
            else:
                if row.high >= stop:
                    exit_price, reason = stop, "STOP"
                elif z <= cfg.exit_z:
                    exit_price, reason = float(row.close), "REVERTED"
            if exit_price is None and bars_held >= cfg.max_holding_bars:
                exit_price, reason = float(row.close), "TIME"
            if exit_price is not None:
                close_out(float(exit_price), int(row.time), reason)

        # --- consider a new entry ---
        if side is None:
            if atr / cfg.pip < cfg.min_atr_pips:
                continue
            if not np.isfinite(row.sd) or row.sd <= 0:
                continue

            want = None
            if z <= -cfg.entry_z:
                want = Signal.BUY          # stretched down -> buy the dip
            elif z >= cfg.entry_z:
                want = Signal.SELL         # stretched up -> fade the rally
            if want is None:
                continue

            # The stop sits at stop_z standard deviations from the mean, so the
            # risk scales with the same dispersion that produced the signal.
            stop_distance = (cfg.stop_z - cfg.entry_z) * float(row.sd)
            if stop_distance <= 0:
                continue
            stop_pips = stop_distance / cfg.pip
            risk_amount = balance * (cfg.risk_percent / 100.0)
            lots = max(0.01, round(
                risk_amount / (stop_pips
                               * instrument.pip_value_per_lot_at(int(row.time))),
                2))

            side = want
            entry = float(row.close)
            entry_time = int(row.time)
            bars_held = 0
            stop = (entry - stop_distance if want is Signal.BUY
                    else entry + stop_distance)

    if side is not None and last_row is not None:
        close_out(float(last_row.close), int(last_row.time), "EOD")

    return CandidateResult(trades=trades, starting_balance=starting_balance,
                           final_balance=round(balance, 2))
