"""Backtest the full MULTI-BOOK setup by replaying the live trading code.

A ``BacktestBroker`` simulates the MT5 client surface over a historical base
dataset (e.g. M5), resampling it to each book's timeframe (H4/D1/M15/M5). We
then step bar-by-bar and call the SAME ``app._run_books`` and trailing-stop code
the live bot uses, so the behaviour you see here is the behaviour you get live.

Costs: each closed trade is charged the spread (round-trip) plus optional
commission, so fast books (scalp/day) are scored honestly. SL/TP fill intrabar
at the level price; if a bar touches both, the stop is taken first.

CLI:
    python -m mt5_ai_bridge.backtest_books data/GBPUSD_M5.csv [--balance 10000]
                                           [--spread 1.5] [--commission 0]
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import numpy as np
import pandas as pd

from .books import build_books

CONTRACT = 100_000
_MISS = object()
PIP = 0.0001
_TF_FREQ = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
            "H1": "1h", "H4": "4h", "D1": "1D"}


class BacktestBroker:
    """A minimal broker simulator with the same surface as RealMT5Client."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self, df: pd.DataFrame, starting_balance: float = 10_000.0,
                 spread_pips: float = 0.0, commission_per_lot: float = 0.0):
        self.df = df.reset_index(drop=True)
        self.balance = starting_balance
        self.spread_pips = spread_pips
        self.commission_per_lot = commission_per_lot
        self._i = 0
        self._ticket = 1000
        self.open: List[dict] = []
        self.closed: List[dict] = []
        self.equity_curve: List[float] = []
        self._resampled = self._resample_all()

    def _resample_all(self):
        idx = pd.to_datetime(self.df["time"], unit="s")
        base = self.df.copy()
        base.index = idx
        out = {}
        for tf, freq in _TF_FREQ.items():
            r = base.resample(freq).agg(open=("open", "first"), high=("high", "max"),
                                        low=("low", "min"), close=("close", "last")).dropna()
            r["time"] = r.index.astype("int64") // 10**9
            out[tf] = r.reset_index(drop=True)
        # Fast lookup indexes (O(log n) per bar instead of a full scan).
        self._tf_records = {tf: rr.to_dict("records") for tf, rr in out.items()}
        self._tf_times = {tf: np.asarray([rec["time"] for rec in recs])
                          for tf, recs in self._tf_records.items()}
        return out

    def _price(self) -> float:
        return float(self.df["close"].iloc[self._i])

    def _bar(self):
        return self.df.iloc[self._i]

    def _floating(self, p) -> float:
        direction = 1 if p["type"] == self.POSITION_TYPE_BUY else -1
        return direction * (self._price() - p["price_open"]) * p["volume"] * CONTRACT

    def _cost(self, volume) -> float:
        return self.spread_pips * PIP * volume * CONTRACT + self.commission_per_lot * volume

    def _close(self, p, price, t, reason):
        direction = 1 if p["type"] == self.POSITION_TYPE_BUY else -1
        gross = direction * (price - p["price_open"]) * p["volume"] * CONTRACT
        profit = gross - self._cost(p["volume"])
        self.balance += profit
        self.closed.append({
            "magic": p["magic"], "side": "BUY" if direction > 0 else "SELL",
            "entry": p["price_open"], "exit": price, "profit": round(profit, 2),
            "reason": reason, "time": t,
        })

    def advance(self, i: int) -> None:
        self._i = i
        bar = self.df.iloc[i]
        still = []
        for p in self.open:
            exit_price = reason = None
            if p["type"] == self.POSITION_TYPE_BUY:
                if p["sl"] and bar["low"] <= p["sl"]:
                    exit_price, reason = p["sl"], "SL"
                elif p["tp"] and bar["high"] >= p["tp"]:
                    exit_price, reason = p["tp"], "TP"
            else:
                if p["sl"] and bar["high"] >= p["sl"]:
                    exit_price, reason = p["sl"], "SL"
                elif p["tp"] and bar["low"] <= p["tp"]:
                    exit_price, reason = p["tp"], "TP"
            if reason:
                self._close(p, exit_price, str(bar["time"]), reason)
            else:
                p["price_current"] = float(bar["close"])
                still.append(p)
        self.open = still

    def initialize(self):
        return True

    def login(self, *a, **k):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def symbol_info(self, symbol):
        return SimpleNamespace(digits=5, point=0.00001)

    def symbol_info_tick(self, symbol):
        p = self._price()
        return SimpleNamespace(bid=p, ask=p)

    def account_info(self):
        eq = self.balance + sum(self._floating(p) for p in self.open)
        return SimpleNamespace(balance=round(self.balance, 2), equity=round(eq, 2),
                               margin=0.0, margin_free=round(eq, 2),
                               profit=round(eq - self.balance, 2), login=0)

    def positions_get(self, **kwargs):
        objs = [SimpleNamespace(
            ticket=p["ticket"], symbol=p["symbol"], type=p["type"],
            volume=p["volume"], price_open=p["price_open"],
            price_current=self._price(), sl=p["sl"], tp=p["tp"],
            magic=p["magic"], profit=self._floating(p)) for p in self.open]
        ticket = kwargs.get("ticket")
        if ticket is not None:
            return [o for o in objs if o.ticket == ticket]
        return objs

    def copy_rates_from_pos(self, symbol, timeframe_name, start, count):
        tf = timeframe_name.upper()
        times = self._tf_times.get(tf)
        if times is None or len(times) == 0:
            return None
        now_t = int(self.df["time"].iloc[self._i])
        idx = int(np.searchsorted(times, now_t, side="right"))
        if idx == 0:
            return None
        return self._tf_records[tf][max(0, idx - count):idx]

    def last_bar_time(self, timeframe_name):
        """Timestamp of the most recent completed bar of ``timeframe_name``."""
        tf = timeframe_name.upper()
        times = self._tf_times.get(tf)
        if times is None or len(times) == 0:
            return None
        now_t = int(self.df["time"].iloc[self._i])
        idx = int(np.searchsorted(times, now_t, side="right"))
        return None if idx == 0 else int(times[idx - 1])

    def order_send(self, request):
        action = request["action"]
        if action == self.TRADE_ACTION_SLTP:
            for p in self.open:
                if p["ticket"] == request.get("position"):
                    p["sl"] = request.get("sl", p["sl"])
                    p["tp"] = request.get("tp", p["tp"])
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE,
                                   order=request.get("position"), comment="sltp")
        if "position" in request:
            tk = request["position"]
            for p in list(self.open):
                if p["ticket"] == tk:
                    self._close(p, request["price"], str(self._bar()["time"]), "MANUAL")
                    self.open.remove(p)
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=tk,
                                   comment="close")
        self._ticket += 1
        ptype = self.POSITION_TYPE_BUY if request["type"] == self.ORDER_TYPE_BUY \
            else self.POSITION_TYPE_SELL
        self.open.append({
            "ticket": self._ticket, "symbol": request["symbol"], "type": ptype,
            "volume": request["volume"], "price_open": request["price"],
            "sl": request.get("sl", 0.0), "tp": request.get("tp", 0.0),
            "magic": request.get("magic", 0), "price_current": request["price"],
        })
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=self._ticket,
                               comment="open")


@dataclass
class BookBacktestResult:
    trades: List[dict]
    equity_curve: List[float]
    starting_balance: float
    final_balance: float
    magic_names: dict = field(default_factory=dict)

    def _stats(self, trades):
        n = len(trades)
        wins = sum(1 for t in trades if t["profit"] > 0)
        pnl = round(sum(t["profit"] for t in trades), 2)
        return {"trades": n, "wins": wins,
                "win_rate": round(wins / n, 3) if n else 0.0, "pnl": pnl}

    @property
    def max_drawdown(self):
        peak = self.starting_balance
        dd = 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            dd = max(dd, peak - e)
        return round(dd, 2)

    def summary(self):
        by_book = defaultdict(list)
        for t in self.trades:
            by_book[self.magic_names.get(t["magic"], f"magic-{t['magic']}")].append(t)
        return {
            "overall": {**self._stats(self.trades),
                        "total_profit": round(self.final_balance - self.starting_balance, 2),
                        "max_drawdown": self.max_drawdown,
                        "starting_balance": round(self.starting_balance, 2),
                        "final_balance": round(self.final_balance, 2)},
            "by_book": {name: self._stats(ts) for name, ts in by_book.items()},
        }


def run_multibook_backtest(df: pd.DataFrame, settings, starting_balance: float = 10_000.0,
                           spread_pips: float = 1.5,
                           commission_per_lot: float = 0.0) -> BookBacktestResult:
    from . import app as _appmod
    from .app import (_run_books, _update_trailing_stops, make_planner_configs,
                      make_strategy)
    from .journal import Journal

    settings = replace(settings, max_trades_per_day=10**9,
                       write_dashboard=False, serve_dashboard=False)
    broker = BacktestBroker(df, starting_balance, spread_pips, commission_per_lot)
    journal = Journal(":memory:")
    strategy_fn = make_strategy(settings)
    planner = make_planner_configs(settings)

    # Snapshot memoization: the indicator snapshot for a timeframe only changes
    # when a new bar of that timeframe closes, so cache it per (symbol, tf,
    # last-bar-time). On a miss we call the ORIGINAL market_snapshot, so results
    # are byte-for-byte identical to the unmemoized backtest -- this is a pure
    # speedup. Restored in ``finally`` so live behaviour is untouched.
    _orig_snapshot = _appmod.market_snapshot
    _snap_memo = {}

    def _memoized_snapshot(client, symbol, timeframe="M30", atr_period=14):
        last = getattr(client, "last_bar_time", lambda _tf: None)(timeframe)
        if last is None:
            return _orig_snapshot(client, symbol, timeframe, atr_period)
        key = (symbol, timeframe.upper(), last)
        cached = _snap_memo.get(key, _MISS)
        if cached is _MISS:
            cached = _orig_snapshot(client, symbol, timeframe, atr_period)
            _snap_memo[key] = cached
        return cached

    _appmod.market_snapshot = _memoized_snapshot
    try:
        times = df["time"].astype("int64").tolist()
        for i in range(len(df)):
            broker.advance(i)
            now = datetime.fromtimestamp(times[i], tz=timezone.utc)
            _run_books(broker, journal, settings, strategy_fn, planner,
                       broker.positions_get(), now_utc=now)
            if settings.trail_enabled:
                _update_trailing_stops(broker, settings)
            broker.equity_curve.append(broker.account_info().equity)
    finally:
        _appmod.market_snapshot = _orig_snapshot

    last = df.iloc[-1]
    for p in list(broker.open):
        broker._close(p, float(last["close"]), str(last["time"]), "EOD")
    broker.open = []
    journal.close()

    magic_names = {b.magic: b.name for b in build_books(settings)}
    return BookBacktestResult(broker.closed, broker.equity_curve,
                              starting_balance, round(broker.balance, 2), magic_names)


def _load_csv_with_time(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={c: c.lower() for c in df.columns})
    for cand in ("time", "date", "datetime", "timestamp"):
        if cand in df.columns:
            col = df[cand]
            df["time"] = pd.to_numeric(col, errors="coerce")
            if df["time"].isna().any():
                df["time"] = pd.to_datetime(col, utc=True).astype("int64") // 10**9
            break
    else:
        raise ValueError("CSV needs a time/date column with real timestamps.")
    return df[["time", "open", "high", "low", "close"]]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mt5_ai_bridge.backtest_books",
                                description="Backtest the multi-book setup.")
    p.add_argument("csv", help="Base-timeframe OHLC CSV with timestamps (e.g. M5)")
    p.add_argument("--balance", type=float, default=10_000)
    p.add_argument("--spread", type=float, default=1.5, help="Spread in pips (round-trip cost)")
    p.add_argument("--commission", type=float, default=0.0, help="$ per lot")
    args = p.parse_args(argv)

    from .config import load_settings
    df = _load_csv_with_time(args.csv)
    result = run_multibook_backtest(df, load_settings(), args.balance,
                                    args.spread, args.commission)
    print(f"Multi-book backtest: {args.csv}  ({len(df)} base bars)  "
          f"spread={args.spread}p commission=${args.commission}/lot")
    print(json.dumps(result.summary(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
