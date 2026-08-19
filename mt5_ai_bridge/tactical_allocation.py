"""Timing an asset you would otherwise hold: in above the average, out below.

Everything measured in this repo says the same thing from a different angle.
The ETF reversion rule earned a profit factor and was beaten by holding on six
of six. Cross-sectional ranking added +1.44% a year at t = 0.815, which is
zero. Six FX engines and gold lost after costs. The one thing that reliably
made money across 22.9 years was **being long the asset**.

So the target changes. Absolute profit is available by holding IVV and needs no
system. What holding does not give you is a tolerable drawdown: buy-and-hold
equity gives up roughly half its value in 2008 and a third in 2020, and this
account's stated ceiling is 10%. A timing rule that keeps most of the return
while avoiding the worst of the falls is worth something; one that merely makes
money is not, because zero effort already does that.

The rule is Faber (2007), unchanged
-----------------------------------
At each month end, hold the asset if its price is above its ``sma_months``
month moving average, otherwise hold cash. Long or flat, never short. Monthly
decisions only, on completed months.

Published parameters, not searched: ten months is the figure in the paper, and
the paper's own claim is drawdown reduction rather than return enhancement.
That is the claim being tested, so the benchmark is buy-and-hold and the metric
that decides it is risk-adjusted, not total return.

Cash earns zero here. That is deliberately pessimistic -- a real account earns
something on cash, and assuming zero cannot flatter the strategy.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

__all__ = ["TacticalConfig", "LOCKED_TACTICAL", "locked_tactical_config",
           "TimingResult", "replay_timing"]

LOCK_PATH = (Path(__file__).resolve().parents[1] / "research"
             / "tactical_locked.json")


@dataclass(frozen=True)
class TacticalConfig:
    sma_months: int = 10
    trading_days_per_month: int = 21
    long_only: bool = True

    @property
    def sma_days(self) -> int:
        return self.sma_months * self.trading_days_per_month

    def validate(self) -> None:
        if self.sma_months < 2:
            raise ValueError("sma_months must be at least 2")
        if self.trading_days_per_month < 1:
            raise ValueError("trading_days_per_month must be positive")
        if not self.long_only:
            raise ValueError(
                "this rule is long-or-flat by construction; shorting an index "
                "below its average is a different strategy with a different "
                "risk profile, and it is not what the published rule tests")


LOCKED_TACTICAL = TacticalConfig()


def locked_tactical_config(path: Optional[Path] = None) -> TacticalConfig:
    """Load the frozen parameters, refusing a lock file edited after the fact."""
    path = Path(path or LOCK_PATH)
    if not path.exists():
        raise FileNotFoundError(f"lock file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cfg = TacticalConfig(**payload.get("parameters", {}))
    cfg.validate()
    if cfg != LOCKED_TACTICAL:
        raise ValueError(
            f"lock file {path} disagrees with the code's LOCKED_TACTICAL.\n"
            f"  file: {asdict(cfg)}\n  code: {asdict(LOCKED_TACTICAL)}")
    return cfg


@dataclass
class TimingResult:
    """Period returns for the timed rule and for holding the same asset."""

    symbol: str = "?"
    strategy: List[float] = field(default_factory=list)
    benchmark: List[float] = field(default_factory=list)
    invested: List[bool] = field(default_factory=list)
    switches: int = 0
    times: List[int] = field(default_factory=list)

    @property
    def periods(self) -> int:
        return len(self.strategy)

    @property
    def time_in_market(self) -> float:
        if not self.invested:
            return 0.0
        return round(sum(self.invested) / len(self.invested), 4)

    @staticmethod
    def _stats(returns: List[float], periods_per_year: float) -> dict:
        if not returns:
            return {"total_pct": 0.0, "annual_pct": 0.0, "volatility_pct": 0.0,
                    "sharpe": 0.0, "max_drawdown_pct": 0.0}
        r = np.asarray(returns, dtype=float)
        growth = float(np.prod(1.0 + r))
        years = len(r) / periods_per_year
        annual = growth ** (1.0 / years) - 1.0 if years > 0 and growth > 0 else -1.0
        vol = float(r.std(ddof=1)) * np.sqrt(periods_per_year) if len(r) > 1 else 0.0
        mean = float(r.mean()) * periods_per_year
        curve = np.concatenate([[1.0], np.cumprod(1.0 + r)])
        peak = np.maximum.accumulate(curve)
        drawdown = float(np.max((peak - curve) / peak)) if len(curve) else 0.0
        return {
            "total_pct": float(round((growth - 1.0) * 100.0, 2)),
            "annual_pct": float(round(annual * 100.0, 3)),
            "volatility_pct": float(round(vol * 100.0, 3)),
            "sharpe": float(round(mean / vol, 3)) if vol > 0 else 0.0,
            "max_drawdown_pct": float(round(drawdown * 100.0, 2)),
        }

    def summary(self, periods_per_year: float = 12.0) -> dict:
        strategy = self._stats(self.strategy, periods_per_year)
        benchmark = self._stats(self.benchmark, periods_per_year)
        return {
            "symbol": self.symbol,
            "periods": self.periods,
            "time_in_market": self.time_in_market,
            "switches": self.switches,
            "strategy": strategy,
            "buy_and_hold": benchmark,
            # The two questions that decide it, both relative to holding.
            # Cast explicitly: these come off numpy scalars, and a numpy bool
            # is not JSON serialisable.
            "beats_hold_on_sharpe": bool(
                strategy["sharpe"] > benchmark["sharpe"]),
            "drawdown_reduction_pct": float(round(
                benchmark["max_drawdown_pct"] - strategy["max_drawdown_pct"], 2)),
        }


def replay_timing(bars: pd.DataFrame, cfg: TacticalConfig = LOCKED_TACTICAL,
                  spread_pct: float = 0.0, symbol: str = "?") -> TimingResult:
    """Replay the moving-average timing rule against holding the same asset.

    The decision at a month end uses only closes up to and including that day,
    and is applied to the *following* month, so no bar informs its own trade.
    Costs are charged on switches only: staying invested two months in a row
    does not pay a spread.
    """
    cfg.validate()
    if "close" not in bars.columns:
        raise ValueError("bars need a close column")

    closes = bars["close"].to_numpy(dtype=float)
    times = (bars["time"].to_numpy(dtype="int64") if "time" in bars.columns
             else np.arange(len(bars), dtype="int64"))
    sma = pd.Series(closes).rolling(cfg.sma_days).mean().to_numpy()

    step = cfg.trading_days_per_month
    result = TimingResult(symbol=symbol)
    holding = False
    for i in range(cfg.sma_days, len(closes) - 1, step):
        j = min(i + step, len(closes) - 1)
        if not np.isfinite(sma[i]) or closes[i] <= 0 or closes[j] <= 0:
            continue

        want = bool(closes[i] > sma[i])
        cost = (spread_pct / 100.0 / 2.0) if want != holding else 0.0
        if want != holding:
            result.switches += 1
        holding = want

        market = float(closes[j]) / float(closes[i]) - 1.0
        result.strategy.append((market if holding else 0.0) - cost)
        result.benchmark.append(market)
        result.invested.append(holding)
        result.times.append(int(times[j]))

    return result
