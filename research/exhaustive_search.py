"""Search hard for a profitable specification, then test whether it was real.

This is the "improve until an edge is found" request, run under a protocol that
makes the answer interpretable.

Protocol
--------
1. The series is split once. The **search set** is the first 60%; the
   **holdout** is the last 40% and is not touched during the search.
2. Every specification in the grid is scored on the search set.
3. The single best is chosen, and evaluated on the holdout **once**.
4. It is deflated by the number of specifications tried.

Every spec is also scored on the holdout, but only to compute one diagnostic
that cannot be gamed: **of the specs that were profitable in-sample, what
fraction stayed profitable out of sample?** If searching finds real edges that
number is high. If searching finds noise it is about 50%, and a profitable
backtest carries no information at all.

    python research/exhaustive_search.py --symbol GBPUSD --cost tight
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v16 import (LOCKED_V16, ReversionConfig,  # noqa: E402
                                         replay_v16)
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import cost_for, instrument_for  # noqa: E402
from mt5_ai_bridge.validation import (TrialRegistry,  # noqa: E402
                                      deflated_sharpe_ratio, sharpe_ratio)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"
REGISTRY = Path(__file__).resolve().parents[1] / "research" / "v15_trials.json"

ENTRY_Z = (1.0, 1.5, 2.0, 2.5, 3.0)
EXIT_Z = (0.0, 0.25, 0.5, 1.0)
LOOKBACK = (10, 20, 30, 50)
STOP_OFFSET = (1.0, 2.0, 3.0)
MAX_HOLD = (20, 60, 120)


def grid():
    for e, x, lb, so, mh in itertools.product(
            ENTRY_Z, EXIT_Z, LOOKBACK, STOP_OFFSET, MAX_HOLD):
        if x >= e:
            continue
        yield {"entry_z": e, "exit_z": x, "lookback": lb,
               "stop_z": e + so, "max_holding_bars": mh}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--cost", default="tight",
                   help="Most favourable realistic tier by default")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    path = DATA_DIR / f"{args.symbol}_{args.timeframe}.csv"
    if not path.exists():
        print(f"No data at {path}")
        return 2
    audit = audit_bars(pd.read_csv(path), args.symbol, args.timeframe)
    bars = load_csv(str(path)).reset_index(drop=True)
    if audit.trusted_from:
        bars = bars[bars["time"] >= audit.trusted_from].reset_index(drop=True)

    split_at = int(len(bars) * 0.6)
    search_set = bars.iloc[:split_at].reset_index(drop=True)
    holdout = bars.iloc[split_at:].reset_index(drop=True)

    inst = instrument_for(args.symbol)
    cost = cost_for(args.symbol, args.cost)
    specs = list(grid())

    print("=" * 80)
    print(f"EXHAUSTIVE SEARCH  |  {args.symbol} {args.timeframe}  |  "
          f"cost={args.cost}")
    print("=" * 80)
    print(f"Search set : {len(search_set)} bars "
          f"({audit.trusted_from_date} onward, first 60%)")
    print(f"Holdout    : {len(holdout)} bars (last 40%, untouched during search)")
    print(f"Grid       : {len(specs)} specifications\n")

    results = []
    for i, spec in enumerate(specs, 1):
        cfg = replace(LOCKED_V16, **spec)
        try:
            cfg.validate()
        except ValueError:
            continue
        r_in = replay_v16(search_set, cfg, cost, instrument=inst)
        r_out = replay_v16(holdout, cfg, cost, instrument=inst)
        results.append({
            **spec,
            "in_net": r_in.net_profit, "in_trades": len(r_in.trades),
            "in_pf": r_in.profit_factor,
            "out_net": r_out.net_profit, "out_trades": len(r_out.trades),
            "out_pf": r_out.profit_factor,
            "out_returns": r_out.returns,
            "out_sharpe": sharpe_ratio(r_out.returns, 1.0),
        })
        if i % 60 == 0:
            print(f"  ...{i}/{len(specs)} evaluated")

    if not results:
        print("No valid specifications.")
        return 2

    # --- what the search found -------------------------------------------
    results.sort(key=lambda r: r["in_net"], reverse=True)
    best = results[0]

    print("\n" + "=" * 80)
    print("WHAT THE SEARCH FOUND (best on the search set)")
    print("=" * 80)
    print(f"  entry_z={best['entry_z']}  exit_z={best['exit_z']}  "
          f"lookback={best['lookback']}  stop_z={best['stop_z']}  "
          f"max_hold={best['max_holding_bars']}")
    print(f"  IN-SAMPLE : net {best['in_net']:+.2f}, PF {best['in_pf']:.3f}, "
          f"{best['in_trades']} trades")

    profitable_in = [r for r in results if r["in_net"] > 0]
    print(f"\n  {len(profitable_in)} of {len(results)} specifications were "
          f"profitable in-sample.")
    print("  Picking the best of those is what 'improve until an edge is "
          "found' means.")

    # --- did it survive? --------------------------------------------------
    print("\n" + "=" * 80)
    print("WHAT IT DID ON DATA IT HAD NEVER SEEN")
    print("=" * 80)
    print(f"  HOLDOUT   : net {best['out_net']:+.2f}, "
          f"PF {best['out_pf']:.3f}, {best['out_trades']} trades")
    verdict = "held up" if best["out_net"] > 0 else "did not survive"
    print(f"  The in-sample winner {verdict}.")

    # --- the diagnostic that cannot be gamed ------------------------------
    survived = [r for r in profitable_in if r["out_net"] > 0]
    rate = len(survived) / len(profitable_in) if profitable_in else 0.0
    print("\n" + "=" * 80)
    print("DOES A PROFITABLE BACKTEST PREDICT ANYTHING HERE?")
    print("=" * 80)
    print(f"  Of the {len(profitable_in)} specs profitable in-sample, "
          f"{len(survived)} stayed profitable")
    print(f"  out of sample: {rate:.1%}")
    print("\n  A coin flip is 50%. If this number is near 50%, then finding a")
    print("  profitable backtest on this data tells you nothing about the "
          "future,")
    print("  and searching harder only produces a more flattering number.")

    top10 = results[:10]
    top10_survived = sum(1 for r in top10 if r["out_net"] > 0)
    print(f"\n  Among the top 10 in-sample specs, {top10_survived}/10 were "
          "profitable out of sample.")

    # --- deflation --------------------------------------------------------
    registry = TrialRegistry(REGISTRY)
    before = registry.count
    registry.record_many(
        [{"search": "v16_grid", "symbol": args.symbol, **{k: s[k] for k in
          ("entry_z", "exit_z", "lookback", "stop_z", "max_holding_bars")}}
         for s in results], label="exhaustive_search")
    n_trials = registry.count

    dsr = deflated_sharpe_ratio(
        best["out_returns"], n_trials=n_trials,
        trial_sharpes=[r["out_sharpe"] for r in results])

    print("\n" + "=" * 80)
    print("DEFLATED VERDICT")
    print("=" * 80)
    print(f"  Trials on record : {before} before this search, "
          f"{n_trials} after")
    print(f"  Deflated Sharpe  : {dsr:.4f}  (gate 0.95)")
    print(f"  Verdict          : "
          f"{'PASS' if dsr >= 0.95 and best['out_net'] > 0 else 'FAIL'}")

    print("\n  Every specification tried is now on record. The deflation bar "
          "rises with")
    print("  the count, which is why searching harder cannot manufacture a "
          "passing result.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "symbol": args.symbol, "timeframe": args.timeframe,
            "cost": args.cost, "n_specs": len(results),
            "n_trials_after": n_trials,
            "profitable_in_sample": len(profitable_in),
            "of_those_profitable_out": len(survived),
            "survival_rate": round(rate, 4),
            "best": {k: v for k, v in best.items() if k != "out_returns"},
            "deflated_sharpe": round(dsr, 4),
            "top10": [{k: v for k, v in r.items() if k != "out_returns"}
                      for r in top10],
        }, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
