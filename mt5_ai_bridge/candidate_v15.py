"""V15: a locked time-series-momentum candidate, designed to be falsifiable.

Why a new candidate at all
--------------------------
Every profile from v4 to v14.25 was tuned on the same history and then judged on
it. ``research/V14_4_TRAIN_CONFIRM_EDGE_REBUILD_REPORT.md`` showed what happens
when that is done honestly: all five selection protocols failed their locked
test. Adding a twenty-sixth tuned profile would repeat the mistake.

So this candidate is built the other way round -- parameters fixed *before*
seeing a result, from two sources that are not this dataset:

**1. Cost arithmetic.** ``research/v14_4_cost_stress_report.py`` shows a 5-pip
scalp at 1.25R dies to ~0.4 pips of cost. The round trip does not shrink, so the
only fix is a bigger target: at a ~1.3 pip round trip, a 60-pip average stop
puts cost at ~2% of risk instead of ~26%. Hence H4 bars, ATR-scaled stops and
days-long holds -- infrequent trades with a large edge-to-cost ratio.

**2. Published effect.** Time-series momentum (Moskowitz, Ooi & Pedersen, 2012)
and Donchian channel breakout are long-documented across futures and FX. The
20/10 entry/exit lookback is the classic Turtle parameterisation, taken as-is
rather than optimised.

Nothing here was chosen because it backtested well. If it fails, it fails
honestly, and that is a usable result.

The parameters are frozen in ``research/v15_locked_candidate.json``;
:func:`locked_config` loads them and refuses to run against a mismatched file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from .costs import ZERO_COST, CostModel
from .enums import Signal

__all__ = [
    "MomentumConfig",
    "LOCKED",
    "locked_config",
    "resample_ohlc",
    "add_channels",
    "replay",
    "CandidateTrade",
    "CandidateResult",
]

LOCK_PATH = Path(__file__).resolve().parents[1] / "research" / "v15_locked_candidate.json"


@dataclass(frozen=True)
class MomentumConfig:
    """Locked parameters. Changing one means it is a new candidate, not a tweak."""

    timeframe_minutes: int = 240        # H4
    entry_lookback: int = 20            # Turtle breakout
    exit_lookback: int = 10             # Turtle trailing exit
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    trend_ema: int = 50                 # regime filter on the same timeframe
    min_atr_pips: float = 8.0           # skip dead volatility
    risk_percent: float = 0.5
    pip: float = 0.0001
    contract_size: float = 100_000.0

    def validate(self) -> None:
        if self.timeframe_minutes < 1:
            raise ValueError("timeframe_minutes must be positive")
        if self.entry_lookback < 2 or self.exit_lookback < 2:
            raise ValueError("lookbacks must be at least 2")
        if self.exit_lookback >= self.entry_lookback:
            raise ValueError("exit_lookback must be shorter than entry_lookback")
        if self.atr_period < 2:
            raise ValueError("atr_period must be at least 2")
        if self.atr_stop_mult <= 0:
            raise ValueError("atr_stop_mult must be positive")
        if self.pip <= 0 or self.contract_size <= 0:
            raise ValueError("pip and contract_size must be positive")


LOCKED = MomentumConfig()


def locked_config(path: Optional[Path] = None) -> MomentumConfig:
    """Load the frozen parameters, verifying they match :data:`LOCKED`.

    A mismatch means someone edited the lock file after the fact, which is the
    exact failure mode this module exists to prevent.
    """
    path = Path(path or LOCK_PATH)
    if not path.exists():
        raise FileNotFoundError(f"lock file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    params = payload.get("parameters", {})
    cfg = MomentumConfig(**params)
    cfg.validate()
    if cfg != LOCKED:
        raise ValueError(
            f"lock file {path} disagrees with the code's LOCKED config.\n"
            f"  file: {asdict(cfg)}\n  code: {asdict(LOCKED)}")
    return cfg


# --- data preparation ------------------------------------------------------


def resample_ohlc(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Resample an epoch-second OHLC frame to ``minutes`` bars."""
    if "time" not in df.columns:
        raise ValueError("dataframe needs a 'time' column of epoch seconds")
    base = df.copy()
    base.index = pd.to_datetime(base["time"], unit="s")
    out = base.resample(f"{minutes}min").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last")).dropna()
    out["time"] = out.index.astype("int64") // 10 ** 9
    return out.reset_index(drop=True)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def add_channels(df: pd.DataFrame, cfg: MomentumConfig) -> pd.DataFrame:
    """Attach Donchian channels, ATR and the trend filter.

    Every column is shifted so a bar's signal uses only bars strictly before
    it -- no look-ahead. ``entry_high`` at row i is the highest high of the
    ``entry_lookback`` bars ending at i-1.
    """
    out = df.copy()
    out["entry_high"] = out["high"].rolling(cfg.entry_lookback).max().shift(1)
    out["entry_low"] = out["low"].rolling(cfg.entry_lookback).min().shift(1)
    out["exit_high"] = out["high"].rolling(cfg.exit_lookback).max().shift(1)
    out["exit_low"] = out["low"].rolling(cfg.exit_lookback).min().shift(1)
    out["atr"] = _atr(out, cfg.atr_period).shift(1)
    out["ema"] = out["close"].ewm(span=cfg.trend_ema, adjust=False).mean().shift(1)
    return out


# --- replay ----------------------------------------------------------------


@dataclass
class CandidateTrade:
    entry_time: int
    exit_time: int
    side: Signal
    entry: float
    exit: float
    lots: float
    pips: float
    profit: float
    cost: float
    reason: str


@dataclass
class CandidateResult:
    trades: List[CandidateTrade]
    starting_balance: float
    final_balance: float

    @property
    def returns(self) -> List[float]:
        return [t.profit for t in self.trades]

    @property
    def net_profit(self) -> float:
        return round(self.final_balance - self.starting_balance, 2)

    @property
    def total_costs(self) -> float:
        return round(sum(t.cost for t in self.trades), 2)

    @property
    def profit_factor(self) -> float:
        win = sum(t.profit for t in self.trades if t.profit > 0)
        loss = -sum(t.profit for t in self.trades if t.profit < 0)
        if loss == 0:
            return float("inf") if win > 0 else 0.0
        return round(win / loss, 3)

    def summary(self) -> dict:
        n = len(self.trades)
        wins = sum(1 for t in self.trades if t.profit > 0)
        return {
            "trades": n,
            "wins": wins,
            "win_rate": round(wins / n, 3) if n else 0.0,
            "net_profit": self.net_profit,
            "total_costs": self.total_costs,
            "profit_factor": self.profit_factor,
            "final_balance": round(self.final_balance, 2),
        }


def replay(bars: pd.DataFrame, cfg: MomentumConfig = LOCKED,
           cost: CostModel = ZERO_COST, starting_balance: float = 10_000.0
           ) -> CandidateResult:
    """Replay the locked candidate over already-resampled bars.

    One position at a time. Entry on a close beyond the entry channel and on
    the right side of the trend filter; exit on the opposite short channel or
    the ATR stop, whichever the bar reaches first (stop assumed first when a
    bar spans both).
    """
    cfg.validate()
    df = add_channels(bars, cfg)
    balance = starting_balance
    trades: List[CandidateTrade] = []

    side: Optional[Signal] = None
    entry = stop = 0.0
    lots = 0.0
    entry_time = 0

    pip_value_per_lot = cfg.pip * cfg.contract_size

    def close_out(exit_price: float, exit_time: int, reason: str) -> None:
        """Book the open position and add it to the ledger."""
        nonlocal balance, side
        direction = 1.0 if side is Signal.BUY else -1.0
        gross = direction * (exit_price - entry) * lots * cfg.contract_size
        nights = max(0, int((exit_time - entry_time) // 86_400))
        trade_cost = (cost.round_trip_pips * lots * pip_value_per_lot
                      + cost.commission_cost(lots)
                      + cost.swap_cost(side, lots, nights, pip_value_per_lot))
        profit = gross - trade_cost
        balance += profit
        trades.append(CandidateTrade(
            entry_time=entry_time, exit_time=exit_time, side=side,
            entry=entry, exit=float(exit_price), lots=lots,
            pips=round(direction * (exit_price - entry) / cfg.pip, 1),
            profit=round(profit, 2), cost=round(trade_cost, 2),
            reason=reason))
        side = None

    last_row = None
    for row in df.itertuples(index=False):
        last_row = row
        atr = getattr(row, "atr")
        if not np.isfinite(atr) or atr <= 0:
            continue

        # --- manage an open position ---
        if side is not None:
            exit_price = reason = None
            if side is Signal.BUY:
                if row.low <= stop:
                    exit_price, reason = stop, "STOP"
                elif np.isfinite(row.exit_low) and row.low <= row.exit_low:
                    exit_price, reason = row.exit_low, "CHANNEL"
            else:
                if row.high >= stop:
                    exit_price, reason = stop, "STOP"
                elif np.isfinite(row.exit_high) and row.high >= row.exit_high:
                    exit_price, reason = row.exit_high, "CHANNEL"

            if exit_price is not None:
                close_out(float(exit_price), int(row.time), reason)

        # --- consider a new entry ---
        if side is None:
            atr_pips = atr / cfg.pip
            if atr_pips < cfg.min_atr_pips:
                continue
            if not (np.isfinite(row.entry_high) and np.isfinite(row.entry_low)
                    and np.isfinite(row.ema)):
                continue

            want = None
            if row.close > row.entry_high and row.close > row.ema:
                want = Signal.BUY
            elif row.close < row.entry_low and row.close < row.ema:
                want = Signal.SELL
            if want is None:
                continue

            stop_distance = cfg.atr_stop_mult * atr
            risk_amount = balance * (cfg.risk_percent / 100.0)
            stop_pips = stop_distance / cfg.pip
            lots = risk_amount / (stop_pips * pip_value_per_lot)
            lots = max(0.01, round(lots, 2))

            side = want
            entry = float(row.close)
            entry_time = int(row.time)
            stop = (entry - stop_distance if want is Signal.BUY
                    else entry + stop_distance)

    # A position still open when the data runs out is marked to the last close.
    # Without this it would vanish from both the ledger and the P&L, which is
    # how a losing trend-follower can look flat.
    if side is not None and last_row is not None:
        close_out(float(last_row.close), int(last_row.time), "EOD")

    return CandidateResult(trades=trades, starting_balance=starting_balance,
                           final_balance=round(balance, 2))
