"""Event-driven backtester.

Replays historical OHLC bars through the SAME indicators and strategy used
live, simulating stop-loss / take-profit fills, and reports trades, an equity
curve and summary statistics.

Design notes / assumptions (v1):
- One open position at a time per run (matches the simple per-position flow of
  the live bot). Multi-position portfolio backtesting is a later phase.
- Indicators are computed once over the whole series. Every indicator here
  (EMA/RSI/MACD) is backward-looking, so reading row ``i`` uses only bars up to
  ``i`` -- no look-ahead bias.
- Entry fills at the close of the signal bar. SL/TP fill at the level price on
  the first subsequent bar whose range touches it. If a bar touches both, the
  stop is assumed hit first (conservative).
- Money P&L = direction * (exit - entry) * lot_size * contract_size.

The decision function is injectable (``strategy_fn``), so the trend strategy,
the reasoning layer, or a test stub can all be backtested with identical
execution semantics.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from .enums import Signal
from .indicators import add_indicators
from .strategy import evaluate_strategy

# Keys that must be valid (non-NaN) before a bar is tradeable. RSI is the
# binding constraint (rolling window); the EMAs/MACD are defined from the start.
_REQUIRED = ("ema_20", "ema_50", "close", "rsi_14", "macd", "macd_signal")

# Full set of indicator values passed to the decision function. A richer
# strategy (e.g. the reasoning layer) reads more of these; extras are harmless.
_MARKET_KEYS = ("close", "ema_9", "ema_20", "ema_50", "ema_200", "rsi_14",
                "macd", "macd_signal", "macd_hist")


@dataclass
class Trade:
    entry_time: str
    side: Signal
    entry_price: float
    exit_time: str
    exit_price: float
    exit_reason: str          # "TP", "SL", or "EOD" (end of data)
    pips: float
    profit: float


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    starting_balance: float = 0.0
    final_balance: float = 0.0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.profit > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.profit <= 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n_trades if self.n_trades else 0.0

    @property
    def total_pips(self) -> float:
        return round(sum(t.pips for t in self.trades), 1)

    @property
    def total_profit(self) -> float:
        return round(self.final_balance - self.starting_balance, 2)

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.profit for t in self.trades if t.profit > 0)
        gross_loss = -sum(t.profit for t in self.trades if t.profit < 0)
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0.0
        return round(gross_win / gross_loss, 2)

    @property
    def max_drawdown(self) -> float:
        """Largest peak-to-trough drop on the equity curve (money)."""
        peak = self.starting_balance
        max_dd = 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)
        return round(max_dd, 2)

    def summary(self) -> dict:
        return {
            "trades": self.n_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 3),
            "total_pips": self.total_pips,
            "total_profit": self.total_profit,
            "profit_factor": self.profit_factor,
            "max_drawdown": self.max_drawdown,
            "starting_balance": round(self.starting_balance, 2),
            "final_balance": round(self.final_balance, 2),
        }


class Backtester:
    def __init__(self, *, pip_size: float = 0.0001, lot_size: float = 0.01,
                 stop_loss_pips: float = 30, take_profit_pips: float = 60,
                 contract_size: float = 100_000, starting_balance: float = 10_000,
                 strategy_fn=evaluate_strategy):
        self.pip_size = pip_size
        self.lot_size = lot_size
        self.stop_loss_pips = stop_loss_pips
        self.take_profit_pips = take_profit_pips
        self.contract_size = contract_size
        self.starting_balance = starting_balance
        # Injectable so the reasoning layer (or a test) can swap the decision
        # function while keeping identical execution/SL-TP semantics.
        self.strategy_fn = strategy_fn

    def _money(self, side: Signal, entry: float, exit_: float) -> float:
        direction = 1.0 if side is Signal.BUY else -1.0
        return direction * (exit_ - entry) * self.lot_size * self.contract_size

    def _pips(self, side: Signal, entry: float, exit_: float) -> float:
        direction = 1.0 if side is Signal.BUY else -1.0
        return round(direction * (exit_ - entry) / self.pip_size, 1)

    def run(self, df: pd.DataFrame) -> BacktestResult:
        df = add_indicators(df.copy())
        if "time" not in df.columns:
            df["time"] = range(len(df))

        result = BacktestResult(starting_balance=self.starting_balance,
                                final_balance=self.starting_balance)
        balance = self.starting_balance

        open_side: Optional[Signal] = None
        entry_price = sl_price = tp_price = 0.0
        entry_time = ""

        for _, bar in df.iterrows():
            # --- manage an open position first ---
            if open_side is not None:
                exit_price = exit_reason = None
                if open_side is Signal.BUY:
                    if bar["low"] <= sl_price:
                        exit_price, exit_reason = sl_price, "SL"
                    elif bar["high"] >= tp_price:
                        exit_price, exit_reason = tp_price, "TP"
                else:  # SELL
                    if bar["high"] >= sl_price:
                        exit_price, exit_reason = sl_price, "SL"
                    elif bar["low"] <= tp_price:
                        exit_price, exit_reason = tp_price, "TP"

                if exit_price is not None:
                    profit = self._money(open_side, entry_price, exit_price)
                    balance += profit
                    result.trades.append(Trade(
                        entry_time=entry_time, side=open_side,
                        entry_price=entry_price, exit_time=str(bar["time"]),
                        exit_price=exit_price, exit_reason=exit_reason,
                        pips=self._pips(open_side, entry_price, exit_price),
                        profit=round(profit, 2),
                    ))
                    open_side = None

            # --- mark-to-market equity at this bar's close ---
            if open_side is not None:
                unrealized = self._money(open_side, entry_price, bar["close"])
                result.equity_curve.append(round(balance + unrealized, 2))
            else:
                result.equity_curve.append(round(balance, 2))

            # --- consider a new entry (only if flat and indicators ready) ---
            if open_side is None and not _row_has_nan(bar):
                decision = self.strategy_fn(_row_to_market(bar))
                if decision.signal.is_trade:
                    open_side = decision.signal
                    entry_price = float(bar["close"])
                    entry_time = str(bar["time"])
                    offset_sl = self.stop_loss_pips * self.pip_size
                    offset_tp = self.take_profit_pips * self.pip_size
                    if open_side is Signal.BUY:
                        sl_price = entry_price - offset_sl
                        tp_price = entry_price + offset_tp
                    else:
                        sl_price = entry_price + offset_sl
                        tp_price = entry_price - offset_tp

        # --- close any still-open position at the last close ---
        if open_side is not None:
            last = df.iloc[-1]
            profit = self._money(open_side, entry_price, float(last["close"]))
            balance += profit
            result.trades.append(Trade(
                entry_time=entry_time, side=open_side, entry_price=entry_price,
                exit_time=str(last["time"]), exit_price=float(last["close"]),
                exit_reason="EOD",
                pips=self._pips(open_side, entry_price, float(last["close"])),
                profit=round(profit, 2),
            ))

        result.final_balance = round(balance, 2)
        return result


def _row_to_market(bar) -> dict:
    market = {"symbol": "BACKTEST", "time": str(bar["time"])}
    for k in _MARKET_KEYS:
        if k in bar.index and not pd.isna(bar[k]):
            market[k] = float(bar[k])
    return market


def _row_has_nan(bar) -> bool:
    return any(pd.isna(bar[k]) for k in _REQUIRED)
