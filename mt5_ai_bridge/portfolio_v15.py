"""Multi-symbol portfolio replay: consistency from structure, not tuning.

What diversification can and cannot do
--------------------------------------
Combining N imperfectly-correlated return streams scales the portfolio's Sharpe
by up to sqrt(N). That is real and it is the only free lunch available here --
but it is a **multiplier on an existing edge**, not a source of one. If each
stream has zero or negative expectancy, diversification does not rescue it; it
just makes the losses smoother and more regular.

So this module deliberately does not try to make unprofitable symbols
profitable. It does three structural things, each of which is standard practice
rather than a fitted choice:

1. **Volatility targeting.** Each position is sized so its risk contribution is
   the same fraction of equity regardless of the instrument's volatility.
   Without it, gold's ATR dwarfs AUDUSD's and the "portfolio" is really one
   gold bet.

2. **Correlated-exposure caps.** FX majors share drivers; four simultaneous
   USD-short positions is one leveraged bet, not four diversified ones. Total
   open risk and per-currency risk are both capped.

3. **Walk-forward symbol admission.** Which symbols to trade in a fold is
   decided using only *prior* folds. This is the honest version of "trade what
   works" -- picking the winners after seeing the whole history is exactly the
   selection bias that produced v4..v14.25.

Admission can additionally require measured trend persistence
(:mod:`mt5_ai_bridge.persistence`), so a symbol that does not trend is never
admitted no matter how flattering its backtest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .candidate_v15 import LOCKED, MomentumConfig, add_channels
from .costs import ZERO_COST, CostModel
from .enums import Signal
from .instruments import instrument_for

__all__ = [
    "PortfolioConfig",
    "PortfolioTrade",
    "PortfolioResult",
    "replay_portfolio",
    "currency_exposure",
    "correlation_matrix",
    "effective_bets",
    "diversification_report",
]


@dataclass(frozen=True)
class PortfolioConfig:
    """Portfolio-level risk structure. Not strategy parameters."""

    risk_percent_per_trade: float = 0.5      # equal risk per position
    max_total_risk_percent: float = 2.0      # aggregate open risk ceiling
    max_currency_risk_percent: float = 1.5   # per-currency factor cap
    max_concurrent_positions: int = 4
    volatility_target: bool = True

    def validate(self) -> None:
        if self.risk_percent_per_trade <= 0:
            raise ValueError("risk_percent_per_trade must be positive")
        if self.max_total_risk_percent < self.risk_percent_per_trade:
            raise ValueError("total risk ceiling below single-trade risk")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be at least 1")


def correlation_matrix(bars_by_symbol: Dict[str, pd.DataFrame]
                       ) -> tuple[List[str], np.ndarray]:
    """Correlation of overlapping log returns across symbols."""
    series = {}
    for symbol, df in bars_by_symbol.items():
        s = pd.Series(np.log(df["close"].to_numpy(dtype=float)).astype(float),
                      index=df["time"].astype("int64"))
        series[symbol] = s.diff().dropna()
    frame = pd.DataFrame(series).dropna()
    symbols = list(frame.columns)
    if frame.empty or len(symbols) < 2:
        return symbols, np.eye(max(len(symbols), 1))
    return symbols, frame.corr().to_numpy()


def effective_bets(corr: np.ndarray) -> float:
    """How many genuinely independent bets an equally-weighted basket holds.

    For N assets with average pairwise correlation rho, this tends to
    ``N / (1 + (N-1) * rho)``: five perfectly correlated symbols are one bet,
    five uncorrelated ones are five. Diversification scales Sharpe by roughly
    the square root of THIS number, not of the symbol count.
    """
    n = corr.shape[0]
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0
    w = np.full(n, 1.0 / n)
    variance = float(w @ corr @ w)
    if variance <= 0:
        return float(n)
    return float(1.0 / variance)


def diversification_report(bars_by_symbol: Dict[str, pd.DataFrame]) -> dict:
    """Whether a symbol set actually diversifies, with the numbers."""
    symbols, corr = correlation_matrix(bars_by_symbol)
    n = len(symbols)
    off = [corr[i][j] for i in range(n) for j in range(i + 1, n)]
    eff = effective_bets(corr)

    quotes: Dict[str, int] = {}
    for s in symbols:
        try:
            _, q = currency_exposure(s)
        except ValueError:
            continue
        quotes[q] = quotes.get(q, 0) + 1

    return {
        "symbols": symbols,
        "n_symbols": n,
        "mean_abs_correlation": round(float(np.mean(np.abs(off))), 4) if off else 0.0,
        "max_abs_correlation": round(float(np.max(np.abs(off))), 4) if off else 0.0,
        "effective_bets": round(eff, 2),
        "diversification_ratio": round(eff / n, 3) if n else 0.0,
        "sharpe_multiplier": round(math.sqrt(eff), 3),
        "shared_quote_currencies": quotes,
        "correlations": {symbols[i]: {symbols[j]: round(float(corr[i][j]), 3)
                                      for j in range(n)}
                         for i in range(n)},
    }


def currency_exposure(symbol: str) -> tuple[str, str]:
    """(base, quote) currencies. XAUUSD -> ('XAU', 'USD')."""
    s = str(symbol).upper()
    if len(s) < 6:
        raise ValueError(f"cannot split {symbol!r} into currencies")
    return s[:3], s[3:6]


@dataclass
class PortfolioTrade:
    symbol: str
    entry_time: int
    exit_time: int
    side: Signal
    entry: float
    exit: float
    lots: float
    profit: float
    cost: float
    reason: str


@dataclass
class PortfolioResult:
    trades: List[PortfolioTrade]
    starting_balance: float
    final_balance: float
    equity_curve: List[float] = field(default_factory=list)
    admitted: Dict[int, List[str]] = field(default_factory=dict)
    rejected_for_risk: int = 0

    @property
    def returns(self) -> List[float]:
        return [t.profit for t in self.trades]

    @property
    def net_profit(self) -> float:
        return round(self.final_balance - self.starting_balance, 2)

    @property
    def profit_factor(self) -> float:
        win = sum(t.profit for t in self.trades if t.profit > 0)
        loss = -sum(t.profit for t in self.trades if t.profit < 0)
        if loss == 0:
            return float("inf") if win > 0 else 0.0
        return round(win / loss, 3)

    @property
    def max_drawdown_percent(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.starting_balance
        dd = 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                dd = max(dd, (peak - eq) / peak)
        return round(dd * 100.0, 2)

    def by_symbol(self) -> dict:
        out: dict = {}
        for t in self.trades:
            rec = out.setdefault(t.symbol, {"trades": 0, "profit": 0.0})
            rec["trades"] += 1
            rec["profit"] = round(rec["profit"] + t.profit, 2)
        return out

    def summary(self) -> dict:
        n = len(self.trades)
        wins = sum(1 for t in self.trades if t.profit > 0)
        return {
            "trades": n,
            "wins": wins,
            "win_rate": round(wins / n, 3) if n else 0.0,
            "net_profit": self.net_profit,
            "profit_factor": self.profit_factor,
            "max_drawdown_percent": self.max_drawdown_percent,
            "total_costs": round(sum(t.cost for t in self.trades), 2),
            "final_balance": round(self.final_balance, 2),
            "rejected_for_risk": self.rejected_for_risk,
        }


def _prepare(bars_by_symbol: Dict[str, pd.DataFrame],
             cfg: MomentumConfig) -> Dict[str, pd.DataFrame]:
    """Attach signals per symbol and index by bar time."""
    prepared = {}
    for symbol, bars in bars_by_symbol.items():
        df = add_channels(bars, cfg)
        df = df.set_index(df["time"].astype("int64"), drop=False)
        prepared[symbol] = df
    return prepared


def replay_portfolio(bars_by_symbol: Dict[str, pd.DataFrame],
                     cfg: MomentumConfig = LOCKED,
                     portfolio: PortfolioConfig = PortfolioConfig(),
                     cost: CostModel = ZERO_COST,
                     starting_balance: float = 10_000.0,
                     admitted: Optional[Sequence[str]] = None
                     ) -> PortfolioResult:
    """Replay the locked signals across symbols on one shared account.

    Symbols are stepped in lock-step on a shared timeline, so risk limits see
    every position that is genuinely open at once -- the thing a per-symbol
    backtest cannot model. ``admitted`` restricts trading to a subset without
    changing anything else, which is how walk-forward admission is applied.
    """
    cfg.validate()
    portfolio.validate()

    symbols = [s for s in bars_by_symbol
               if admitted is None or s in set(admitted)]
    if not symbols:
        return PortfolioResult([], starting_balance, starting_balance, [])

    instruments = {s: instrument_for(s) for s in symbols}
    prepared = _prepare({s: bars_by_symbol[s] for s in symbols}, cfg)

    timeline = sorted({int(t) for df in prepared.values()
                       for t in df["time"].astype("int64")})

    balance = starting_balance
    equity_curve: List[float] = []
    trades: List[PortfolioTrade] = []
    open_positions: Dict[str, dict] = {}
    rejected = 0

    for now in timeline:
        # --- manage open positions -------------------------------------
        for symbol in list(open_positions):
            df = prepared[symbol]
            if now not in df.index:
                continue
            row = df.loc[now]
            if isinstance(row, pd.DataFrame):       # duplicate timestamps
                row = row.iloc[0]
            pos = open_positions[symbol]
            inst = instruments[symbol]
            side = pos["side"]

            exit_price = reason = None
            if side is Signal.BUY:
                if row["low"] <= pos["stop"]:
                    exit_price, reason = pos["stop"], "STOP"
                elif np.isfinite(row["exit_low"]) and row["low"] <= row["exit_low"]:
                    exit_price, reason = row["exit_low"], "CHANNEL"
            else:
                if row["high"] >= pos["stop"]:
                    exit_price, reason = pos["stop"], "STOP"
                elif np.isfinite(row["exit_high"]) and row["high"] >= row["exit_high"]:
                    exit_price, reason = row["exit_high"], "CHANNEL"

            if exit_price is None:
                continue

            direction = 1.0 if side is Signal.BUY else -1.0
            lots = pos["lots"]
            pip_value = inst.pip_value_per_lot
            gross = direction * (exit_price - pos["entry"]) * lots * inst.contract_size
            nights = max(0, int((now - pos["entry_time"]) // 86_400))
            trade_cost = (cost.round_trip_pips * lots * pip_value
                          + cost.commission_cost(lots)
                          + cost.swap_cost(side, lots, nights, pip_value))
            profit = gross - trade_cost
            balance += profit
            trades.append(PortfolioTrade(
                symbol=symbol, entry_time=pos["entry_time"], exit_time=now,
                side=side, entry=pos["entry"], exit=float(exit_price),
                lots=lots, profit=round(profit, 2),
                cost=round(trade_cost, 2), reason=reason))
            del open_positions[symbol]

        # --- consider new entries ---------------------------------------
        for symbol in symbols:
            if symbol in open_positions:
                continue
            if len(open_positions) >= portfolio.max_concurrent_positions:
                break
            df = prepared[symbol]
            if now not in df.index:
                continue
            row = df.loc[now]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]

            inst = instruments[symbol]
            atr = row["atr"]
            if not np.isfinite(atr) or atr <= 0:
                continue
            if atr / inst.pip < cfg.min_atr_pips:
                continue
            if not (np.isfinite(row["entry_high"]) and np.isfinite(row["entry_low"])
                    and np.isfinite(row["ema"])):
                continue

            want = None
            if row["close"] > row["entry_high"] and row["close"] > row["ema"]:
                want = Signal.BUY
            elif row["close"] < row["entry_low"] and row["close"] < row["ema"]:
                want = Signal.SELL
            if want is None:
                continue

            # --- portfolio risk gates -----------------------------------
            risk_pct = portfolio.risk_percent_per_trade
            open_risk = sum(p["risk_percent"] for p in open_positions.values())
            if open_risk + risk_pct > portfolio.max_total_risk_percent + 1e-9:
                rejected += 1
                continue

            base, quote = currency_exposure(symbol)
            ccy_risk: Dict[str, float] = {}
            for p in open_positions.values():
                b, q = currency_exposure(p["symbol"])
                ccy_risk[b] = ccy_risk.get(b, 0.0) + p["risk_percent"]
                ccy_risk[q] = ccy_risk.get(q, 0.0) + p["risk_percent"]
            if (ccy_risk.get(base, 0.0) + risk_pct
                    > portfolio.max_currency_risk_percent + 1e-9
                    or ccy_risk.get(quote, 0.0) + risk_pct
                    > portfolio.max_currency_risk_percent + 1e-9):
                rejected += 1
                continue

            # --- volatility-targeted size --------------------------------
            stop_distance = cfg.atr_stop_mult * atr
            stop_pips = stop_distance / inst.pip
            pip_value = inst.pip_value_per_lot
            if portfolio.volatility_target:
                risk_amount = balance * (risk_pct / 100.0)
                lots = risk_amount / (stop_pips * pip_value)
            else:
                lots = cfg.risk_percent / 100.0
            lots = max(0.01, round(lots, 2))

            entry = float(row["close"])
            open_positions[symbol] = {
                "symbol": symbol, "side": want, "entry": entry,
                "entry_time": now, "lots": lots, "risk_percent": risk_pct,
                "stop": (entry - stop_distance if want is Signal.BUY
                         else entry + stop_distance),
            }

        # --- mark to market ---------------------------------------------
        floating = 0.0
        for symbol, pos in open_positions.items():
            df = prepared[symbol]
            if now not in df.index:
                continue
            row = df.loc[now]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            inst = instruments[symbol]
            direction = 1.0 if pos["side"] is Signal.BUY else -1.0
            floating += (direction * (float(row["close"]) - pos["entry"])
                         * pos["lots"] * inst.contract_size)
        equity_curve.append(round(balance + floating, 2))

    # --- close whatever is still open at the end -------------------------
    for symbol, pos in list(open_positions.items()):
        df = prepared[symbol]
        last = df.iloc[-1]
        inst = instruments[symbol]
        direction = 1.0 if pos["side"] is Signal.BUY else -1.0
        lots = pos["lots"]
        pip_value = inst.pip_value_per_lot
        gross = direction * (float(last["close"]) - pos["entry"]) * lots * inst.contract_size
        nights = max(0, int((int(last["time"]) - pos["entry_time"]) // 86_400))
        trade_cost = (cost.round_trip_pips * lots * pip_value
                      + cost.commission_cost(lots)
                      + cost.swap_cost(pos["side"], lots, nights, pip_value))
        profit = gross - trade_cost
        balance += profit
        trades.append(PortfolioTrade(
            symbol=symbol, entry_time=pos["entry_time"],
            exit_time=int(last["time"]), side=pos["side"], entry=pos["entry"],
            exit=float(last["close"]), lots=lots, profit=round(profit, 2),
            cost=round(trade_cost, 2), reason="EOD"))

    trades.sort(key=lambda t: t.exit_time)
    return PortfolioResult(trades=trades, starting_balance=starting_balance,
                           final_balance=round(balance, 2),
                           equity_curve=equity_curve,
                           rejected_for_risk=rejected)
