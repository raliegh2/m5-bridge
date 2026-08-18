"""Shared-account V16 replay across the ETFs this account can actually trade.

Why this exists separately from :mod:`portfolio_v15`
----------------------------------------------------
That module caps correlated exposure by *currency*, splitting a symbol into a
base and a quote pair. ``currency_exposure("ONEQ")`` raises -- an ETF ticker is
not six characters and its risk is not a currency bet. Every one of these six
is quoted in USD, so a per-currency cap would do identically nothing while the
positions all rise and fall together.

The factor that actually matters here is equity beta, and it binds hard:

* IVV and VTI correlate **0.989** -- they are one instrument with two tickers.
* VTI/IWM 0.909, IVV/IWM 0.883.
* Equally weighted, the six carry **1.49 effective bets**, not six. Six
  simultaneous positions is one US-equity position at roughly six times size.

So exposure is capped per factor, and TQQQ counts three times its notional
because it is a 3x fund: one TQQQ position is three units of Nasdaq risk, and
a cap that treats it as one unit is not a cap.

Sizing on a real account
------------------------
One ETF lot is one share, and shares are indivisible. The broker minimum is one
share, so a position is either at least one share of risk or it does not exist.
:func:`replay_etf_portfolio` floors to whole shares and refuses any entry whose
single-share risk already exceeds the per-trade budget, rather than silently
rounding up past it -- the same failure the FX sizing fix closed.

Data must be split-adjusted before it gets here; see
:mod:`mt5_ai_bridge.corporate_actions`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .candidate_v16 import LOCKED_V16, ReversionConfig, add_bands
from .costs import ZERO_COST, CostModel
from .enums import Signal
from .instruments import Instrument, instrument_for, settle

__all__ = ["FACTORS", "factor_of", "EtfPortfolioConfig", "EtfTrade",
           "EtfPortfolioResult", "replay_etf_portfolio"]

# symbol -> (factor, beta units of that factor per unit of notional risk)
FACTORS: Dict[str, Tuple[str, float]] = {
    "ONEQ": ("US_EQUITY", 1.0),
    "IVV": ("US_EQUITY", 1.0),
    "IWM": ("US_EQUITY", 1.0),
    "VTI": ("US_EQUITY", 1.0),
    "TQQQ": ("US_EQUITY", 3.0),   # 3x leveraged Nasdaq
    "EEM": ("EM_EQUITY", 1.0),    # correlates 0.77 with IVV, not 0.99
}


def factor_of(symbol: str) -> Tuple[str, float]:
    """(factor, beta) for ``symbol``; unknown tickers get their own factor."""
    return FACTORS.get(str(symbol).upper(), (str(symbol).upper(), 1.0))


@dataclass(frozen=True)
class EtfPortfolioConfig:
    """Risk structure. Not strategy parameters -- those are locked in V16."""

    risk_percent_per_trade: float = 0.5
    max_total_risk_percent: float = 1.5
    max_factor_risk_percent: float = 1.0
    max_concurrent_positions: int = 3
    min_shares: int = 1
    # Divide a position's risk budget by its factor beta, so every position
    # contributes the same number of factor units. Without it a 3x fund asks
    # for three units against a one-unit cap and can NEVER be admitted -- TQQQ
    # took zero trades in 22.9 years, which reads as a decision and is not one.
    beta_scaled_risk: bool = True

    def validate(self) -> None:
        if self.risk_percent_per_trade <= 0:
            raise ValueError("risk_percent_per_trade must be positive")
        if self.max_total_risk_percent < self.risk_percent_per_trade:
            raise ValueError("total risk ceiling below single-trade risk")
        if self.max_factor_risk_percent <= 0:
            raise ValueError("max_factor_risk_percent must be positive")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be at least 1")
        if self.min_shares < 1:
            raise ValueError("min_shares must be at least one share")


@dataclass(frozen=True)
class EtfTrade:
    symbol: str
    side: Signal
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    shares: int
    risk_percent: float
    profit: float
    cost: float
    reason: str


@dataclass
class EtfPortfolioResult:
    trades: List[EtfTrade]
    starting_balance: float
    final_balance: float
    equity_curve: List[float] = field(default_factory=list)
    rejected: Dict[str, int] = field(default_factory=dict)

    @property
    def net_profit(self) -> float:
        return round(self.final_balance - self.starting_balance, 2)

    @property
    def return_percent(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return round(self.net_profit / self.starting_balance * 100.0, 2)

    @property
    def profit_factor(self) -> Optional[float]:
        gains = sum(t.profit for t in self.trades if t.profit > 0)
        losses = -sum(t.profit for t in self.trades if t.profit < 0)
        return round(gains / losses, 3) if losses > 0 else None

    @property
    def max_drawdown_percent(self) -> float:
        if not self.equity_curve:
            return 0.0
        curve = np.asarray(self.equity_curve, dtype=float)
        peak = np.maximum.accumulate(curve)
        with np.errstate(divide="ignore", invalid="ignore"):
            drawdown = np.where(peak > 0, (peak - curve) / peak, 0.0)
        return round(float(drawdown.max()) * 100.0, 2)

    def by_symbol(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for trade in self.trades:
            row = out.setdefault(trade.symbol,
                                 {"trades": 0, "net_profit": 0.0, "wins": 0})
            row["trades"] += 1
            row["net_profit"] = round(row["net_profit"] + trade.profit, 2)
            row["wins"] += 1 if trade.profit > 0 else 0
        return out


def _prepare(bars_by_symbol: Dict[str, pd.DataFrame],
             cfg: ReversionConfig) -> Dict[str, pd.DataFrame]:
    prepared = {}
    for symbol, df in bars_by_symbol.items():
        frame = add_bands(df, cfg)
        frame = frame.assign(time=frame["time"].astype("int64"))
        prepared[symbol] = frame.set_index("time", drop=False)
    return prepared


def replay_etf_portfolio(bars_by_symbol: Dict[str, pd.DataFrame],
                         cfg: ReversionConfig = LOCKED_V16,
                         portfolio: EtfPortfolioConfig = EtfPortfolioConfig(),
                         cost: CostModel = ZERO_COST,
                         starting_balance: float = 4_802.43,
                         costs_by_symbol: Optional[Dict[str, CostModel]] = None,
                         instruments: Optional[Dict[str, Instrument]] = None
                         ) -> EtfPortfolioResult:
    """Replay V16 across several ETFs on one shared account.

    Symbols advance on a shared timeline, so the risk caps see every position
    that is genuinely open at the same moment -- which is the whole point, and
    the thing six independent per-symbol backtests cannot show.
    """
    cfg.validate()
    portfolio.validate()

    symbols = list(bars_by_symbol)
    costs_by_symbol = costs_by_symbol or {}
    instruments = instruments or {s: instrument_for(s) for s in symbols}
    prepared = _prepare(bars_by_symbol, cfg)
    timeline = sorted({int(t) for frame in prepared.values()
                       for t in frame["time"]})

    balance = starting_balance
    equity_curve: List[float] = []
    trades: List[EtfTrade] = []
    open_positions: Dict[str, dict] = {}
    rejected: Dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    def close_out(symbol: str, position: dict, price: float, when: int,
                  reason: str) -> None:
        nonlocal balance
        nights = max(0, int((when - position["entry_time"]) // 86_400))
        gross, trade_cost = settle(
            instruments[symbol], position["side"], float(position["shares"]),
            position["entry"], float(price), nights,
            costs_by_symbol.get(symbol, cost), when)
        profit = gross - trade_cost
        balance += profit
        trades.append(EtfTrade(
            symbol=symbol, side=position["side"],
            entry_time=position["entry_time"], exit_time=when,
            entry=position["entry"], exit=float(price),
            shares=position["shares"],
            risk_percent=round(position["risk_percent"], 3),
            profit=round(profit, 2), cost=round(trade_cost, 2), reason=reason))
        del open_positions[symbol]

    for now in timeline:
        # --- exits first: capacity freed this bar is available this bar -----
        for symbol in list(open_positions):
            frame = prepared[symbol]
            if now not in frame.index:
                continue
            row = frame.loc[now]
            position = open_positions[symbol]
            position["bars_held"] += 1
            price = reason = None
            if position["side"] is Signal.BUY:
                if float(row.low) <= position["stop"]:
                    price, reason = position["stop"], "STOP"
                elif np.isfinite(row.z) and row.z >= -cfg.exit_z:
                    price, reason = float(row.close), "REVERTED"
            else:
                if float(row.high) >= position["stop"]:
                    price, reason = position["stop"], "STOP"
                elif np.isfinite(row.z) and row.z <= cfg.exit_z:
                    price, reason = float(row.close), "REVERTED"
            if price is None and position["bars_held"] >= cfg.max_holding_bars:
                price, reason = float(row.close), "TIME"
            if price is not None:
                close_out(symbol, position, float(price), now, reason)

        # --- entries --------------------------------------------------------
        for symbol in symbols:
            if symbol in open_positions:
                continue
            frame = prepared[symbol]
            if now not in frame.index:
                continue
            row = frame.loc[now]
            if not (np.isfinite(row.z) and np.isfinite(row.atr)
                    and np.isfinite(row.sd)):
                continue
            if row.atr <= 0 or row.sd <= 0:
                continue
            instrument = instruments[symbol]
            if row.atr / instrument.pip < cfg.min_atr_pips:
                continue

            if row.z <= -cfg.entry_z:
                side = Signal.BUY
            elif row.z >= cfg.entry_z:
                side = Signal.SELL
            else:
                continue

            if len(open_positions) >= portfolio.max_concurrent_positions:
                reject("max_positions")
                continue

            stop_distance = (cfg.stop_z - cfg.entry_z) * float(row.sd)
            if stop_distance <= 0:
                continue

            factor, beta = factor_of(symbol)
            trade_risk_percent = portfolio.risk_percent_per_trade
            if portfolio.beta_scaled_risk and beta > 0:
                trade_risk_percent /= beta

            # One share is the smallest tradable unit. If a single share
            # already risks more than the budget, the trade does not fit on
            # this account -- rounding up would quietly exceed the limit.
            budget = balance * (trade_risk_percent / 100.0)
            risk_per_share = stop_distance * instrument.contract_size
            if risk_per_share <= 0:
                continue
            shares = floor(budget / risk_per_share)
            if shares < portfolio.min_shares:
                reject("below_min_lot")
                continue

            risk_percent = shares * risk_per_share / balance * 100.0
            open_risk = sum(p["risk_percent"] for p in open_positions.values())
            if open_risk + risk_percent > portfolio.max_total_risk_percent + 1e-9:
                reject("total_risk")
                continue

            factor_risk = sum(
                p["risk_percent"] * p["beta"]
                for p in open_positions.values() if p["factor"] == factor)
            if (factor_risk + risk_percent * beta
                    > portfolio.max_factor_risk_percent + 1e-9):
                reject("factor_risk_" + factor)
                continue

            entry = float(row.close)
            open_positions[symbol] = {
                "side": side, "entry": entry, "entry_time": now,
                "shares": int(shares), "risk_percent": risk_percent,
                "bars_held": 0, "factor": factor, "beta": beta,
                "stop": (entry - stop_distance if side is Signal.BUY
                         else entry + stop_distance),
            }

        equity_curve.append(balance)

    # Flatten anything still open on each symbol's last bar.
    for symbol in list(open_positions):
        frame = prepared[symbol]
        last = frame.iloc[-1]
        close_out(symbol, open_positions[symbol], float(last.close),
                  int(last.time), "EOD")

    return EtfPortfolioResult(trades=trades, starting_balance=starting_balance,
                              final_balance=round(balance, 2),
                              equity_curve=equity_curve, rejected=rejected)
