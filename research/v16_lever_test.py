"""Two pre-specified levers on the V16 signal: cost tier and timeframe.

Neither changes the strategy. V16's parameters stay exactly as locked; only the
broker cost assumption and the bar size vary. That matters because a parameter
you did not choose cannot be overfitted -- these are measurements of the same
specification under different conditions, not a search.

V16's shortfall on GBPUSD was $1.005 gross per trade against $1.15 of cost: a
13% gap. If the real spread is tighter than the 'typical' preset assumes, or if
a different timeframe produces a larger effect per trade, the same signal turns
over. That is worth checking before concluding anything.

    python research/v16_lever_test.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v16 import locked_config_v16, replay_v16  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import (cost_for, instrument_for,  # noqa: E402
                                       quote_currency_of)
from mt5_ai_bridge.persistence import log_returns, variance_ratio  # noqa: E402
from mt5_ai_bridge.validation import (FoldResult, WalkForwardReport,  # noqa: E402
                                      walk_forward_splits)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"

TIMEFRAMES = ("D1", "H4", "H1")
TIERS = ("zero", "tight", "typical", "wide")


def load(symbol: str, timeframe: str):
    path = DATA_DIR / f"{symbol}_{timeframe}.csv"
    if not path.exists():
        return None
    audit = audit_bars(pd.read_csv(path), symbol, timeframe)
    if not audit.usable:
        return None
    df = load_csv(str(path)).reset_index(drop=True)
    if audit.trusted_from:
        df = df[df["time"] >= audit.trusted_from].reset_index(drop=True)
    return df if len(df) >= 1000 else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+",
                   default=["GBPUSD", "EURUSD", "AUDUSD"])
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cfg = locked_config_v16()
    print("V16 locked parameters, unchanged. Only cost tier and timeframe vary.")
    print(f"Symbols: {', '.join(args.symbols)} "
          "(the VR<1.0 mean-reverting set)\n")

    rows = []
    for timeframe in TIMEFRAMES:
        for symbol in args.symbols:
            bars = load(symbol, timeframe)
            if bars is None:
                continue
            try:
                inst = instrument_for(symbol)
            except ValueError:
                continue
            if quote_currency_of(symbol) != "USD":
                continue

            try:
                vr = variance_ratio(log_returns(bars["close"].tolist()), 30)
                vr_txt = f"{vr.ratio:.3f}"
            except ValueError:
                vr_txt = "-"

            splits = walk_forward_splits(len(bars), n_folds=args.folds,
                                         train_frac=0.6, embargo=cfg.lookback)
            for tier in TIERS:
                cost = cost_for(symbol, tier)
                folds = []
                for split in splits:
                    r = replay_v16(
                        bars.iloc[split.test_slice()].reset_index(drop=True),
                        cfg, cost, instrument=inst)
                    folds.append(FoldResult(split=split,
                                            net_profit=r.net_profit,
                                            trades=len(r.trades),
                                            returns=r.returns))
                wf = WalkForwardReport(folds=folds, n_trials=1)
                full = replay_v16(bars, cfg, cost, instrument=inst)
                per_trade = (full.net_profit / len(full.trades)
                             if full.trades else 0.0)
                rows.append({
                    "timeframe": timeframe, "symbol": symbol, "tier": tier,
                    "vr": vr_txt, "bars": len(bars),
                    "trades": len(full.trades),
                    "net": full.net_profit,
                    "per_trade": round(per_trade, 3),
                    "oos_net": wf.net_profit, "oos_trades": wf.trades,
                    "oos_pf": wf.profit_factor,
                    "folds_pos": wf.positive_fold_fraction,
                })

    head = (f"{'tf':<4}{'symbol':<8}{'tier':<9}{'VR':>7}{'trades':>8}"
            f"{'net':>11}{'$/trade':>9}{'OOS net':>11}{'OOS PF':>8}{'folds+':>8}")
    print(head)
    print("-" * len(head))
    last_tf = None
    for r in rows:
        if last_tf and r["timeframe"] != last_tf:
            print("-" * len(head))
        last_tf = r["timeframe"]
        print(f"{r['timeframe']:<4}{r['symbol']:<8}{r['tier']:<9}{r['vr']:>7}"
              f"{r['trades']:>8}{r['net']:>11.2f}{r['per_trade']:>9.3f}"
              f"{r['oos_net']:>11.2f}{r['oos_pf']:>8.3f}{r['folds_pos']:>7.0%}")

    winners = [r for r in rows
               if r["oos_net"] > 0 and r["oos_pf"] >= 1.10
               and r["oos_trades"] >= 200 and r["folds_pos"] > 0.5]
    print("\n" + "=" * 78)
    print("COMBINATIONS CLEARING ALL PROFIT GATES OUT OF SAMPLE")
    print("=" * 78)
    if winners:
        for w in winners:
            print(f"  {w['timeframe']} {w['symbol']} @ {w['tier']}: "
                  f"OOS net {w['oos_net']:+.2f}, PF {w['oos_pf']:.3f}, "
                  f"{w['oos_trades']} trades, {w['folds_pos']:.0%} folds")
        print("\n  NOTE: 12 tier/timeframe combinations were examined per "
              "symbol. Any winner\n  here still owes a deflated-Sharpe test "
              "against the full trial count.")
    else:
        print("  None. The signal does not turn over at any cost tier or "
              "timeframe tested.")

    zero = [r for r in rows if r["tier"] == "zero" and r["oos_net"] > 0]
    print(f"\nProfitable out of sample at ZERO cost: {len(zero)}/"
          f"{len([r for r in rows if r['tier'] == 'zero'])}")
    print("  If a row is negative even at zero cost, no broker will fix it.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
