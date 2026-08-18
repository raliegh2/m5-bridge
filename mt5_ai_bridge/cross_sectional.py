"""Cross-sectional equity momentum: the one test this account has never run.

Every profile in this repo has traded a handful of instruments against their
own history -- a *time-series* bet, which asks "is this instrument going up?"
On six correlated ETFs that question has one answer, and the strategy that
appeared to answer it was harvesting drift: it earned a fine profit factor and
was beaten by holding on six of six.

A **cross-sectional** bet asks a different question: "which of these 400 names
is going up *relative to the others*?" Long the winners and short the losers in
equal measure and the market's own direction cancels, so the result cannot be
drift. It is either a real ranking edge or it is nothing, and buy-and-hold is
no longer the benchmark that beats it -- zero is, honestly this time, because
the position is market-neutral.

The specification is Jegadeesh & Titman (1993), unchanged
------------------------------------------------------------
* rank on the return from ``lookback`` to ``skip`` days ago -- twelve months
  skipping the most recent one, because the skipped month carries short-term
  reversal that works against momentum;
* rebalance every ``holding_days``;
* long the top ``n_positions``, short the bottom ``n_positions``, equal weight.

These numbers are the published ones. They are not searched here, and that
matters: this is **one trial**, so a result does not have to survive deflation
against a family of variants the way V15's did. The parameters are frozen in
``research/cross_sectional_locked.json`` and :func:`locked_config` refuses to
run if the file and the code disagree.

What would make this fail honestly
----------------------------------
Costs. A monthly rebalance of 2N positions turns the whole book over twelve
times a year, and every leg pays the spread twice. At a 0.2% round trip and
100% turnover a month, costs alone are ~2.4% a year before the signal does
anything. The replay charges them explicitly.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

__all__ = ["CrossSectionalConfig", "LOCKED_CS", "locked_config",
           "build_panel", "momentum_scores", "CrossSectionalResult",
           "replay_cross_sectional"]

LOCK_PATH = (Path(__file__).resolve().parents[1] / "research"
             / "cross_sectional_locked.json")


@dataclass(frozen=True)
class CrossSectionalConfig:
    lookback_days: int = 252      # twelve months
    skip_days: int = 21           # skip the most recent month
    holding_days: int = 21        # monthly rebalance
    n_positions: int = 20         # each side
    min_names: int = 60           # need a cross-section to rank at all
    market_neutral: bool = True

    def validate(self) -> None:
        if self.lookback_days <= self.skip_days:
            raise ValueError("lookback must exceed the skip window")
        if self.skip_days < 0:
            raise ValueError("skip_days cannot be negative")
        if self.holding_days < 1:
            raise ValueError("holding_days must be positive")
        if self.n_positions < 1:
            raise ValueError("n_positions must be positive")
        if self.min_names < 2 * self.n_positions:
            raise ValueError(
                "min_names must cover both sides of the book; ranking the top "
                "and bottom 20 of 30 names is not a cross-section")


LOCKED_CS = CrossSectionalConfig()


def locked_config(path: Optional[Path] = None) -> CrossSectionalConfig:
    """Load the frozen parameters, refusing a lock file edited after the fact."""
    path = Path(path or LOCK_PATH)
    if not path.exists():
        raise FileNotFoundError(f"lock file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg = CrossSectionalConfig(**payload.get("parameters", {}))
    cfg.validate()
    if cfg != LOCKED_CS:
        raise ValueError(
            f"lock file {path} disagrees with the code's LOCKED_CS.\n"
            f"  file: {asdict(cfg)}\n  code: {asdict(LOCKED_CS)}")
    return cfg


def build_panel(bars_by_symbol: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Close prices as one time x symbol panel, aligned on shared timestamps.

    Bars must already be split-adjusted; see
    :mod:`mt5_ai_bridge.corporate_actions`.
    """
    series = {}
    for symbol, frame in bars_by_symbol.items():
        s = pd.Series(frame["close"].to_numpy(dtype=float),
                      index=frame["time"].astype("int64"))
        series[symbol] = s[~s.index.duplicated(keep="last")]
    panel = pd.DataFrame(series).sort_index()
    return panel


def momentum_scores(panel: pd.DataFrame,
                    cfg: CrossSectionalConfig = LOCKED_CS) -> pd.DataFrame:
    """Return over ``lookback`` days ending ``skip`` days ago, per symbol.

    Both endpoints are strictly in the past at the row's own timestamp, so a
    score never sees the bar it will be traded on.
    """
    past = panel.shift(cfg.skip_days)
    older = panel.shift(cfg.lookback_days)
    with np.errstate(divide="ignore", invalid="ignore"):
        return past / older - 1.0


@dataclass
class CrossSectionalResult:
    period_returns: List[float] = field(default_factory=list)
    period_times: List[int] = field(default_factory=list)
    long_returns: List[float] = field(default_factory=list)
    short_returns: List[float] = field(default_factory=list)
    cost_drag: List[float] = field(default_factory=list)
    names_per_period: List[int] = field(default_factory=list)
    starting_balance: float = 10_000.0

    @property
    def equity_curve(self) -> List[float]:
        balance = self.starting_balance
        curve = []
        for r in self.period_returns:
            balance *= (1.0 + r)
            curve.append(balance)
        return curve

    @property
    def final_balance(self) -> float:
        curve = self.equity_curve
        return round(curve[-1] if curve else self.starting_balance, 2)

    @property
    def net_profit(self) -> float:
        return round(self.final_balance - self.starting_balance, 2)

    @property
    def return_percent(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return round(self.net_profit / self.starting_balance * 100.0, 2)

    @property
    def periods(self) -> int:
        return len(self.period_returns)

    @property
    def hit_rate(self) -> float:
        if not self.period_returns:
            return 0.0
        wins = sum(1 for r in self.period_returns if r > 0)
        return round(wins / len(self.period_returns), 4)

    def annualised(self, periods_per_year: float = 12.0) -> dict:
        """Geometric annual return, volatility and Sharpe of the period series."""
        if not self.period_returns:
            return {"return_pct": 0.0, "volatility_pct": 0.0, "sharpe": 0.0}
        r = np.asarray(self.period_returns, dtype=float)
        growth = float(np.prod(1.0 + r))
        years = len(r) / periods_per_year
        annual = growth ** (1.0 / years) - 1.0 if years > 0 and growth > 0 else -1.0
        vol = float(r.std(ddof=1)) * np.sqrt(periods_per_year) if len(r) > 1 else 0.0
        mean = float(r.mean()) * periods_per_year
        return {"return_pct": round(annual * 100.0, 3),
                "volatility_pct": round(vol * 100.0, 3),
                "sharpe": round(mean / vol, 3) if vol > 0 else 0.0}

    @property
    def max_drawdown_percent(self) -> float:
        curve = np.asarray([self.starting_balance] + self.equity_curve,
                           dtype=float)
        peak = np.maximum.accumulate(curve)
        with np.errstate(divide="ignore", invalid="ignore"):
            dd = np.where(peak > 0, (peak - curve) / peak, 0.0)
        return round(float(dd.max()) * 100.0, 2)


def replay_cross_sectional(panel: pd.DataFrame,
                           cfg: CrossSectionalConfig = LOCKED_CS,
                           spread_pct: Optional[Dict[str, float]] = None,
                           starting_balance: float = 10_000.0,
                           start_index: Optional[int] = None,
                           end_index: Optional[int] = None
                           ) -> CrossSectionalResult:
    """Replay the ranking book over a price panel.

    Each rebalance ranks every name with a finite score and a tradable price at
    both ends of the holding window, takes the extremes, and holds for
    ``holding_days``. The book is rebuilt from scratch each period, so turnover
    is charged on both sides of every leg -- which is the honest treatment for
    a monthly-rebalanced strategy and the thing most likely to sink it.
    """
    cfg.validate()
    spread_pct = spread_pct or {}
    scores = momentum_scores(panel, cfg)
    prices = panel

    first = cfg.lookback_days + 1 if start_index is None else start_index
    last = len(panel) - 1 if end_index is None else min(end_index, len(panel) - 1)

    result = CrossSectionalResult(starting_balance=starting_balance)
    step = cfg.holding_days
    for i in range(max(first, cfg.lookback_days + 1), last, step):
        j = min(i + step, last)
        row = scores.iloc[i]
        entry = prices.iloc[i]
        exit_ = prices.iloc[j]

        eligible = row.index[row.notna() & entry.notna() & exit_.notna()
                             & (entry > 0) & (exit_ > 0)]
        if len(eligible) < cfg.min_names:
            continue
        ranked = row[eligible].sort_values()
        losers = list(ranked.index[:cfg.n_positions])
        winners = list(ranked.index[-cfg.n_positions:])

        def leg_return(names: Sequence[str], sign: float) -> float:
            if not names:
                return 0.0
            gross = np.array([float(exit_[n]) / float(entry[n]) - 1.0
                              for n in names], dtype=float)
            return float(sign * gross.mean())

        long_r = leg_return(winners, 1.0)
        short_r = leg_return(losers, -1.0)

        # Every name is opened and closed, so each leg pays its round trip.
        traded = winners + losers
        cost = float(np.mean([spread_pct.get(n, 0.0) / 100.0
                              for n in traded])) if traded else 0.0
        if cfg.market_neutral:
            gross_period = 0.5 * (long_r + short_r)
            cost_period = cost          # both halves trade, weights sum to 1
        else:
            gross_period = long_r
            cost_period = cost
        net = gross_period - cost_period

        result.period_returns.append(net)
        result.period_times.append(int(panel.index[j]))
        result.long_returns.append(long_r)
        result.short_returns.append(short_r)
        result.cost_drag.append(cost_period)
        result.names_per_period.append(len(eligible))

    return result
