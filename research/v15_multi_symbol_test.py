"""Test the locked V15 candidate across every exported symbol, honestly.

Running one specification against six symbols is six trials, not one. If the
best symbol is then singled out, its result must be deflated by six -- otherwise
this becomes exactly the selection bias that produced v4..v14.25.

This script therefore:

* replays the SAME locked parameters on every symbol (nothing is tuned),
* reports each symbol gross and net so cost sensitivity is visible,
* deflates the BEST symbol by the full number of symbols tried, and
* applies the locked gates to that best symbol.

Run (after tools/export_validation_history.py):
    python research/v15_multi_symbol_test.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_ai_bridge.candidate_v15 import locked_config, replay  # noqa: E402
from mt5_ai_bridge.costs import ZERO_COST, preset  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.instruments import instrument_for  # noqa: E402
from mt5_ai_bridge.validation import (FoldResult, WalkForwardReport,  # noqa: E402
                                      deflated_sharpe_ratio, evaluate,
                                      sharpe_ratio, walk_forward_splits)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"


def walk_forward(bars, cfg, cost, folds: int, instrument):
    splits = walk_forward_splits(len(bars), n_folds=folds, train_frac=0.6,
                                 embargo=cfg.entry_lookback)
    out = []
    for split in splits:
        r = replay(bars.iloc[split.test_slice()], cfg, cost,
                   instrument=instrument)
        out.append(FoldResult(split=split, net_profit=r.net_profit,
                              trades=len(r.trades), returns=r.returns))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--cost", default="typical")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cfg = locked_config()
    cost = preset(args.cost)

    files = sorted(DATA_DIR.glob(f"*_{args.timeframe}.csv"))
    if not files:
        print(f"No {args.timeframe} files in {DATA_DIR}. "
              "Run tools/export_validation_history.py first.")
        return 2

    print(f"Locked V15 on {len(files)} symbols  |  cost={args.cost} "
          f"({cost.round_trip_pips:.2f}p round trip)")
    print("Nothing is tuned per symbol; the parameters are identical "
          "everywhere.\n")

    header = (f"{'symbol':<9}{'bars':>7}{'trades':>8}{'gross':>11}"
              f"{'net':>11}{'net PF':>8}{'OOS net':>11}{'folds+':>8}")
    print(header)
    print("-" * len(header))

    results = {}
    oos_sharpes = []
    skipped = {}
    for path in files:
        symbol = path.stem.rsplit("_", 1)[0]
        try:
            instrument = instrument_for(symbol)
        except ValueError as exc:
            # Refuse rather than price it with the wrong conventions.
            skipped[symbol] = str(exc)
            continue
        bars = load_csv(str(path))
        gross = replay(bars, cfg, ZERO_COST, instrument=instrument)
        net = replay(bars, cfg, cost, instrument=instrument)
        folds = walk_forward(bars, cfg, cost, args.folds, instrument)
        report = WalkForwardReport(folds=folds, n_trials=len(files))
        oos_returns = report.all_returns
        oos_sharpes.append(sharpe_ratio(oos_returns, periods_per_year=1.0))
        results[symbol] = {
            "bars": len(bars),
            "trades": len(net.trades),
            "gross_profit": gross.net_profit,
            "net_profit": net.net_profit,
            "net_profit_factor": net.profit_factor,
            "costs": net.total_costs,
            "oos_net": report.net_profit,
            "oos_trades": report.trades,
            "oos_profit_factor": report.profit_factor,
            "positive_fold_fraction": report.positive_fold_fraction,
            "_returns": oos_returns,
        }
        r = results[symbol]
        print(f"{symbol:<9}{r['bars']:>7}{r['trades']:>8}"
              f"{r['gross_profit']:>11.2f}{r['net_profit']:>11.2f}"
              f"{r['net_profit_factor']:>8.3f}{r['oos_net']:>11.2f}"
              f"{r['positive_fold_fraction']:>7.0%}")

    if skipped:
        print("\nREFUSED (conventions not configured -- see instruments.py):")
        for symbol, why in skipped.items():
            print(f"  {symbol}: {why}")

    if not results:
        print("\nNo priceable symbols. Nothing to conclude.")
        return 2

    # --- the honest part: deflate the winner by how many symbols were tried --
    n_trials = len(results)
    best = max(results, key=lambda s: results[s]["oos_net"])
    b = results[best]
    dsr = deflated_sharpe_ratio(b["_returns"], n_trials=n_trials,
                                trial_sharpes=oos_sharpes)

    metrics = {
        "trades": b["oos_trades"],
        "net_profit": b["oos_net"],
        "profit_factor": b["oos_profit_factor"],
        "positive_fold_fraction": b["positive_fold_fraction"],
        "deflated_sharpe": round(dsr, 4),
    }
    verdict = evaluate(metrics)

    print(f"\nBest symbol out of sample: {best}")
    for k, v in metrics.items():
        print(f"  {k:<24}{v}")
    print(f"  {'n_trials (symbols)':<24}{n_trials}")
    print("\n" + "=" * 60)
    print("VERDICT ON THE BEST SYMBOL, DEFLATED BY ALL SYMBOLS TRIED")
    print("=" * 60)
    print(verdict.explain())

    profitable = [s for s, r in results.items() if r["oos_net"] > 0]
    print(f"\nSymbols profitable out of sample: {len(profitable)}/{n_trials}"
          + (f"  ({', '.join(profitable)})" if profitable else ""))
    total_gross = sum(r["gross_profit"] for r in results.values())
    total_net = sum(r["net_profit"] for r in results.values())
    print(f"Across all symbols, full sample: gross {total_gross:+.2f}  "
          f"net {total_net:+.2f}  "
          f"(costs {sum(r['costs'] for r in results.values()):.2f})")

    if args.json_out:
        payload = {"timeframe": args.timeframe, "cost_preset": args.cost,
                   "n_symbols": n_trials, "skipped": skipped,
                   "best_symbol": best,
                   "best_metrics": metrics,
                   "verdict": {"passed": verdict.passed,
                               "failed_gates": list(verdict.failed_gates)},
                   "by_symbol": {s: {k: v for k, v in r.items()
                                     if not k.startswith("_")}
                                 for s, r in results.items()}}
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
        print(f"\nWrote {args.json_out}")

    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
