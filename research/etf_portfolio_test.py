"""The tradable-ETF portfolio: one shared account, real balance, real caps.

The index CFDs this strategy was designed against (USTEC, US500, US30) are all
``trade_mode = DISABLED`` on this account, and so are QQQ, SPY and DIA. Six
ETFs are fully tradable and carry 22.9 years of history, so this is the version
of the model that could actually be ordered here.

Three things this measures that six independent per-symbol backtests cannot:

1. **Shared capital.** Every position competes for one $4,802.43 balance.
2. **Factor exposure.** The six carry 1.49 effective bets, not six. Positions
   are capped per equity factor, with the 3x fund counted three times.
3. **Whole shares.** One lot is one share and shares are indivisible, so a
   trade whose minimum size exceeds the risk budget does not happen.

Prices are back-adjusted for splits first. Left raw, ONEQ's 2021 ten-for-one
split is a -90% bar that the reversion rule buys as the largest dip on record.

    python research/etf_portfolio_test.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5_ai_bridge.corporate_actions import adjust_for_splits  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.etf_portfolio import (EtfPortfolioConfig,  # noqa: E402
                                         factor_of, replay_etf_portfolio)
from mt5_ai_bridge.instruments import cost_for  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"
ETFS = ("ONEQ", "IVV", "IWM", "VTI", "TQQQ", "EEM")
BALANCE = 4_802.43          # the real account, not the $10,000 of the backtests


def load_adjusted(symbols) -> dict:
    """Split-adjusted, audited bars for each symbol."""
    bars = {}
    for symbol in symbols:
        path = DATA_DIR / f"{symbol}_D1.csv"
        if not path.exists():
            continue
        frame, splits = adjust_for_splits(
            load_csv(str(path)).reset_index(drop=True))
        audit = audit_bars(frame, symbol, "D1")
        if not audit.usable:
            print(f"  {symbol}: UNUSABLE -- "
                  + "; ".join(str(i) for i in audit.fatal))
            continue
        if splits:
            print(f"  {symbol}: back-adjusted {len(splits)} split(s)")
        bars[symbol] = frame
    return bars


def buy_and_hold(frame: pd.DataFrame) -> float:
    return (float(frame["close"].iloc[-1]) / float(frame["close"].iloc[0])
            - 1.0) * 100.0


def walk_forward_admission(bars: dict, costs: dict, folds: int,
                           portfolio: EtfPortfolioConfig) -> dict:
    """Trade only symbols that were profitable in *earlier* folds.

    Choosing the winners after seeing the whole history is the selection bias
    that produced twenty-five discarded profiles. Admission here uses strictly
    prior folds, so fold k is genuinely out of sample for the decision that
    picked its symbols. Fold 0 has no prior evidence and trades everything.
    """
    timeline = np.array(sorted({int(t) for f in bars.values()
                                for t in f["time"]}))
    edges = np.linspace(0, len(timeline), folds + 1, dtype=int)
    cumulative: dict = {s: 0.0 for s in bars}
    balance = BALANCE
    all_trades, admitted_log = [], []

    for k in range(folds):
        start, end = timeline[edges[k]], timeline[edges[k + 1] - 1]
        admitted = ([s for s, pnl in cumulative.items() if pnl > 0] if k
                    else list(bars))
        admitted_log.append({"fold": k, "admitted": sorted(admitted),
                             "start": int(start), "end": int(end)})
        if not admitted:
            continue
        window = {s: bars[s][(bars[s]["time"] >= start)
                             & (bars[s]["time"] <= end)].reset_index(drop=True)
                  for s in admitted}
        window = {s: f for s, f in window.items() if len(f) > 60}
        if not window:
            continue
        result = replay_etf_portfolio(
            window, portfolio=portfolio, starting_balance=balance,
            costs_by_symbol={s: costs[s] for s in window})
        balance = result.final_balance
        all_trades.extend(result.trades)
        for symbol, row in result.by_symbol().items():
            cumulative[symbol] = cumulative.get(symbol, 0.0) + row["net_profit"]

    net = balance - BALANCE
    return {"folds": folds, "trades": len(all_trades),
            "net_profit": round(net, 2),
            "return_percent": round(net / BALANCE * 100.0, 2),
            "final_balance": round(balance, 2), "admission": admitted_log}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", nargs="+", default=list(ETFS))
    parser.add_argument("--cost", default="tight")
    parser.add_argument("--balance", type=float, default=BALANCE)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    print("=" * 88)
    print("TRADABLE-ETF PORTFOLIO -- shared account, real balance, real caps")
    print("=" * 88)
    bars = load_adjusted(args.symbols)
    if not bars:
        print("No usable data.")
        return 2
    costs = {s: cost_for(s, args.cost) for s in bars}

    print(f"\nAccount ${args.balance:,.2f} | {len(bars)} ETFs | cost tier "
          f"{args.cost}\n")
    print(f"{'symbol':<7}{'factor':>11}{'beta':>6}{'years':>7}"
          f"{'buy & hold':>12}")
    print("-" * 43)
    for symbol, frame in bars.items():
        factor, beta = factor_of(symbol)
        years = (frame["time"].iloc[-1] - frame["time"].iloc[0]) / (
            365.25 * 86_400)
        print(f"{symbol:<7}{factor:>11}{beta:>6.1f}{years:>7.1f}"
              f"{buy_and_hold(frame):>11.1f}%")

    payload = {"balance": args.balance, "cost_tier": args.cost,
               "symbols": list(bars),
               "buy_and_hold_percent": {s: round(buy_and_hold(f), 2)
                                        for s, f in bars.items()}}

    print("\n" + "=" * 88)
    print("FULL-SAMPLE SHARED ACCOUNT")
    print("=" * 88)
    for label, cfg in (("beta-scaled risk", EtfPortfolioConfig()),
                       ("flat risk",
                        EtfPortfolioConfig(beta_scaled_risk=False))):
        result = replay_etf_portfolio(
            bars, portfolio=cfg, starting_balance=args.balance,
            costs_by_symbol=costs)
        print(f"\n{label}: {len(result.trades)} trades  "
              f"net {result.net_profit:+,.2f} ({result.return_percent:+.2f}%)  "
              f"PF {result.profit_factor}  max DD "
              f"{result.max_drawdown_percent}%")
        for symbol, row in sorted(result.by_symbol().items()):
            print(f"    {symbol:<6}{row['trades']:>5} trades  "
                  f"net {row['net_profit']:>+10,.2f}")
        print(f"    rejected: {result.rejected}")
        payload[label.replace(" ", "_").replace("-", "_")] = {
            "trades": len(result.trades), "net_profit": result.net_profit,
            "return_percent": result.return_percent,
            "profit_factor": result.profit_factor,
            "max_drawdown_percent": result.max_drawdown_percent,
            "by_symbol": result.by_symbol(), "rejected": result.rejected,
        }

    print("\n" + "=" * 88)
    print(f"WALK-FORWARD ADMISSION ({args.folds} folds)")
    print("=" * 88)
    forward = walk_forward_admission(bars, costs, args.folds,
                                     EtfPortfolioConfig())
    for row in forward["admission"]:
        window = pd.to_datetime([row["start"], row["end"]], unit="s", utc=True)
        print(f"  fold {row['fold']}  {window[0]:%Y-%m}..{window[1]:%Y-%m}  "
              f"admitted: {', '.join(row['admitted']) or '(none)'}")
    print(f"\n  {forward['trades']} trades  net {forward['net_profit']:+,.2f} "
          f"({forward['return_percent']:+.2f}%)  final "
          f"${forward['final_balance']:,.2f}")
    payload["walk_forward"] = forward

    print("\n" + "=" * 88)
    print("READING")
    print("=" * 88)
    best_hold = max(payload["buy_and_hold_percent"].values())
    full = payload["beta_scaled_risk"]
    print(f"  Full-sample portfolio return     : "
          f"{full['return_percent']:+.2f}%  (max DD "
          f"{full['max_drawdown_percent']}%)")
    print(f"  Walk-forward admitted return     : "
          f"{forward['return_percent']:+.2f}%")
    print(f"  Best single buy-and-hold         : {best_hold:+,.1f}%")
    print("  A shared account, factor caps and whole-share sizing do not")
    print("  create an edge -- they bound the damage of not having one.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2),
                                       encoding="utf-8")
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
