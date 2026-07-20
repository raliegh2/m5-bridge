"""Shared-account PORTFOLIO backtest across multiple symbols.

Replays several symbols' M5 CSVs on ONE account, stepping a global clock and
calling the SAME ``app._run_books`` / trailing / risk code the live bot uses,
so the combined open-risk ceiling, shared ``MAX_OPEN_POSITIONS`` and the
account-level dollar stops are all enforced exactly as live. This is the
multi-symbol counterpart to ``backtest_books`` (which is single-symbol).

Per-symbol economics: JPY-quote pairs use pip 0.01 and P&L converted to the
account (USD) currency via ``--usdjpy``; USD-quote pairs use pip 0.0001 and no
conversion. Spreads are per symbol (``SYM=spread`` in ``--spreads``).

CLI:
    python -m mt5_ai_bridge.backtest_portfolio GBPUSD=gbpusd.csv EURUSD=eurusd.csv \
        USDJPY=usdjpy.csv --balance 5000 --ceiling 2.5 --usdjpy 152

A single-symbol portfolio run reproduces the single-symbol ``backtest_books``
result (asserted in the tests), so this shares its execution semantics.
"""

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, List

import numpy as np
import pandas as pd

from . import app as _app
from .backtest_books import CONTRACT, BacktestBroker, _load_csv_with_time
from .config import load_settings
from .indicators import market_snapshot as _real_snapshot
from .journal import Journal
from .risk_engine import DailyLossTracker, RiskLimits, check_risk


def pip_and_conv(symbol: str, usdjpy: float) -> tuple:
    """(pip size, quote->USD factor) for ``symbol``."""
    if symbol.upper().endswith("JPY"):
        return 0.01, (1.0 / usdjpy if usdjpy else 1.0)
    return 0.0001, 1.0


class PortfolioBroker:
    """One shared account over many per-symbol datasets (RealMT5Client surface)."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self, data: Dict[str, pd.DataFrame], balance: float,
                 specs: Dict[str, tuple]):
        # specs[sym] = (pip, conv, spread_pips)
        self.subs = {s: BacktestBroker(df, balance, 0.0, 0.0)
                     for s, df in data.items()}
        self.specs = specs
        self.balance = balance
        self.open: List[dict] = []
        self.closed: List[dict] = []
        self.equity_curve: List[float] = []
        self._ticket = 1000

    # --- helpers ---
    def _price(self, sym: str) -> float:
        sub = self.subs[sym]
        return float(sub.df["close"].iloc[sub._i])

    def _pnl(self, p: dict, price: float) -> float:
        pip, conv, spread = self.specs[p["symbol"]]
        direction = 1 if p["type"] == self.POSITION_TYPE_BUY else -1
        gross = direction * (price - p["price_open"]) * p["volume"] * CONTRACT * conv
        cost = spread * pip * p["volume"] * CONTRACT * conv
        return gross - cost

    def set_time(self, t: int) -> None:
        for sub in self.subs.values():
            ts = sub.df["time"].values
            idx = int(np.searchsorted(ts, t, side="right")) - 1
            sub._i = max(0, idx)

    def process_fills(self) -> None:
        still = []
        for p in self.open:
            sub = self.subs[p["symbol"]]
            bar = sub.df.iloc[sub._i]
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
                self.balance += self._pnl(p, exit_price)
                self.closed.append({"magic": p["magic"], "symbol": p["symbol"],
                                    "profit": round(self._pnl(p, exit_price), 2),
                                    "reason": reason})
            else:
                still.append(p)
        self.open = still

    # --- RealMT5Client surface ---
    def symbol_info(self, symbol):
        pip = self.specs[symbol][0]
        digits = 3 if abs(pip - 0.01) < 1e-9 else 5
        return SimpleNamespace(digits=digits, point=pip / 10.0)

    def symbol_info_tick(self, symbol):
        p = self._price(symbol)
        return SimpleNamespace(bid=p, ask=p)

    def copy_rates_from_pos(self, symbol, timeframe_name, start, count):
        return self.subs[symbol].copy_rates_from_pos(symbol, timeframe_name,
                                                     start, count)

    def last_bar_time(self, symbol, timeframe_name):
        sub = self.subs[symbol]
        times = sub._tf_times.get(timeframe_name.upper())
        if times is None or len(times) == 0:
            return None
        now_t = int(sub.df["time"].iloc[sub._i])
        idx = int(np.searchsorted(times, now_t, side="right"))
        return None if idx == 0 else int(times[idx - 1])

    def account_info(self):
        eq = self.balance + sum(self._pnl(p, self._price(p["symbol"]))
                                + self.specs[p["symbol"]][2] * self.specs[p["symbol"]][0]
                                * p["volume"] * CONTRACT * self.specs[p["symbol"]][1]
                                for p in self.open)
        return SimpleNamespace(balance=round(self.balance, 2), equity=round(eq, 2),
                               margin=0.0, margin_free=round(eq, 2),
                               profit=round(eq - self.balance, 2), login=0)

    def positions_get(self, symbol=None, ticket=None, **kwargs):
        objs = [SimpleNamespace(
            ticket=p["ticket"], symbol=p["symbol"], type=p["type"],
            volume=p["volume"], price_open=p["price_open"],
            price_current=self._price(p["symbol"]), sl=p["sl"], tp=p["tp"],
            magic=p["magic"], profit=self._pnl(p, self._price(p["symbol"])))
            for p in self.open]
        if ticket is not None:
            return [o for o in objs if o.ticket == ticket]
        if symbol is not None:
            return [o for o in objs if o.symbol == symbol]
        return objs

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
                    self.balance += self._pnl(p, request["price"])
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
            "magic": request.get("magic", 0),
        })
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=self._ticket,
                               comment="open")


def run_portfolio_backtest(data, settings, starting_balance=5000.0,
                           spreads=None, usdjpy=152.0):
    """Replay ``data`` ({symbol: OHLC df}) on one shared account. Returns a dict."""
    spreads = spreads or {}
    specs = {}
    for sym in data:
        pip, conv = pip_and_conv(sym, usdjpy)
        specs[sym] = (pip, conv, float(spreads.get(sym, 1.0)))

    settings = replace(settings, symbols=tuple(data.keys()), multi_book=True,
                       max_trades_per_day=10**9, write_dashboard=False,
                       serve_dashboard=False, console_status=False)
    broker = PortfolioBroker(data, starting_balance, specs)
    journal = Journal(":memory:")
    strategy_fn = _app.make_strategy(settings)
    planner = _app.make_planner_configs(settings)
    limits = RiskLimits(settings.daily_max_loss, settings.total_max_loss,
                        settings.max_open_positions)
    tracker = DailyLossTracker()

    # Faithful, memoized snapshot: cache per (symbol, tf, last-bar-time); on a
    # miss call the REAL market_snapshot, so behaviour matches the live engine.
    memo = {}
    orig_snapshot = _app.market_snapshot

    def snap(client, symbol, timeframe="M30", atr_period=14):
        last = client.last_bar_time(symbol, timeframe) \
            if hasattr(client, "last_bar_time") else None
        if last is None:
            return orig_snapshot(client, symbol, timeframe, atr_period)
        key = (symbol, timeframe.upper(), last)
        if key not in memo:
            memo[key] = orig_snapshot(client, symbol, timeframe, atr_period)
        return memo[key]

    _app.market_snapshot = snap
    try:
        times = sorted(set().union(*[set(df["time"].tolist()) for df in data.values()]))
        for t in times:
            broker.set_time(t)
            broker.process_fills()
            account = broker.account_info()
            day_loss = tracker.update(account.equity)
            risk = check_risk(account, broker.positions_get(), limits,
                              daily_loss=day_loss)
            now = datetime.fromtimestamp(t, tz=timezone.utc)
            if risk.ok:
                for sym in data:
                    _app._run_books(broker, journal, settings, strategy_fn,
                                    planner, broker.positions_get(),
                                    now_utc=now, account=account, symbol=sym)
            for sym in data:
                _app._update_trailing_stops(broker, settings, symbol=sym)
            broker.equity_curve.append(broker.account_info().equity)
    finally:
        _app.market_snapshot = orig_snapshot

    for p in list(broker.open):
        broker.balance += broker._pnl(p, broker._price(p["symbol"]))
    broker.open = []
    journal.close()

    net = round(broker.balance - starting_balance, 2)
    peak = starting_balance
    dd = 0.0
    for e in broker.equity_curve:
        peak = max(peak, e)
        dd = max(dd, peak - e)
    by_symbol = {}
    for c in broker.closed:
        by_symbol.setdefault(c["symbol"], 0.0)
        by_symbol[c["symbol"]] += c["profit"]
    return {
        "starting_balance": starting_balance,
        "final_balance": round(broker.balance, 2),
        "net_profit": net,
        "return_pct": round(100 * net / starting_balance, 2),
        "max_drawdown": round(dd, 2),
        "max_drawdown_pct": round(100 * dd / starting_balance, 2),
        "trades": len(broker.closed),
        "by_symbol": {k: round(v, 2) for k, v in by_symbol.items()},
    }


def _parse_specs(items):
    data, order = {}, []
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected SYMBOL=path.csv, got {item!r}")
        sym, path = item.split("=", 1)
        data[sym.upper()] = _load_csv_with_time(path)
        order.append(sym.upper())
    return data


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mt5_ai_bridge.backtest_portfolio",
                                description="Shared-account multi-symbol backtest.")
    p.add_argument("symbols", nargs="+", help="SYMBOL=path.csv (one per pair)")
    p.add_argument("--balance", type=float, default=5000.0)
    p.add_argument("--ceiling", type=float, default=None,
                   help="Override COMBINED_RISK_CEILING (%)")
    p.add_argument("--usdjpy", type=float, default=152.0,
                   help="USDJPY rate for JPY-pair P&L conversion")
    p.add_argument("--spreads", default="",
                   help="Comma list SYM=pips, e.g. EURUSD=0.4,USDJPY=0.8")
    args = p.parse_args(argv)

    data = _parse_specs(args.symbols)
    spreads = {}
    for tok in args.spreads.split(","):
        if "=" in tok:
            s, v = tok.split("=", 1)
            spreads[s.strip().upper()] = float(v)

    settings = load_settings()
    if args.ceiling is not None:
        settings = replace(settings, combined_risk_ceiling=args.ceiling)
    result = run_portfolio_backtest(data, settings, args.balance, spreads,
                                    args.usdjpy)
    print(f"Portfolio backtest: {', '.join(data)}  balance=${args.balance:g}  "
          f"ceiling={settings.combined_risk_ceiling}%")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
