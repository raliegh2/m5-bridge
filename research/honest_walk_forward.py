"""Honest walk-forward evaluation of the reasoning strategy.

This is the counterpart to every other script in ``research/``: instead of
searching a parameter grid over the whole history and reporting the winner, it

1. searches the grid on each fold's **training** slice only,
2. scores the winner on the **following, unseen** slice,
3. repeats across folds, and
4. deflates the result by how many parameter sets were tried
   (:func:`mt5_ai_bridge.validation.deflated_sharpe_ratio`).

It prints both numbers side by side -- the in-sample best (what the older
reports measured) and the out-of-sample walk-forward result (what you would
actually have earned) -- so the size of the selection bias is visible rather
than inferred.

Run:
    python research/honest_walk_forward.py --csv GBPUSD_M5.csv
    python research/honest_walk_forward.py --csv GBPUSD_M5.csv --cost zero
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_ai_bridge.backtest import Backtester  # noqa: E402
from mt5_ai_bridge.costs import preset  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.reasoning import ReasoningConfig, ReasoningStrategy  # noqa: E402
from mt5_ai_bridge.validation import (FoldResult, run_walk_forward,  # noqa: E402
                                      sharpe_ratio, walk_forward_splits)

# The parameter grid IS the trial count that gets deflated away. Widening it
# makes an in-sample winner easier to find and a real edge harder to claim --
# which is the honest trade-off the older reports never priced in.
THRESHOLDS = (0.4, 0.5, 0.6, 0.7)
STOPS = (10.0, 15.0, 20.0, 30.0)
TARGETS = (15.0, 30.0, 45.0, 60.0)

GRID = [{"threshold": t, "sl": s, "tp": p}
        for t, s, p in itertools.product(THRESHOLDS, STOPS, TARGETS)
        if p > s]          # a target below the stop is not a strategy


def backtest(df, params, cost, balance=10_000.0):
    strategy = ReasoningStrategy(ReasoningConfig(threshold=params["threshold"]))
    bt = Backtester(stop_loss_pips=params["sl"], take_profit_pips=params["tp"],
                    starting_balance=balance, strategy_fn=strategy, cost=cost)
    return bt.run(df)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", default="GBPUSD_M5.csv")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--train-frac", type=float, default=0.6)
    p.add_argument("--embargo", type=int, default=250,
                   help="Bars dropped between train and test (indicator lookback)")
    p.add_argument("--cost", default="typical",
                   help="Cost preset: zero / tight / typical / wide")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cost = preset(args.cost)
    df = load_csv(args.csv).reset_index(drop=True)
    n = len(df)
    print(f"Data: {args.csv}  {n} bars")
    print(f"Cost: {args.cost} (round trip {cost.round_trip_pips:.2f} pips)")
    print(f"Grid: {len(GRID)} parameter sets  |  folds: {args.folds}\n")

    # --- what the old reports would have reported ---------------------------
    # Best parameter set chosen on the WHOLE history, scored on that same
    # history. This is in-sample by construction.
    in_sample = []
    trial_sharpes = []
    for params in GRID:
        r = backtest(df, params, cost)
        rets = [t.profit for t in r.trades]
        trial_sharpes.append(sharpe_ratio(rets, periods_per_year=1.0))
        in_sample.append((r.total_profit, params, r))
    in_sample.sort(key=lambda x: x[0], reverse=True)
    best_profit, best_params, best_result = in_sample[0]

    print("=" * 72)
    print("IN-SAMPLE BEST  (how the existing research/ reports were produced)")
    print("=" * 72)
    print(f"  params      : {best_params}")
    print(f"  net profit  : {best_profit:+.2f}")
    print(f"  profit factor: {best_result.profit_factor}")
    print(f"  trades      : {best_result.n_trades}")
    print("  This number is not evidence. It is the maximum of "
          f"{len(GRID)} tries on one history.\n")

    # --- the honest version -------------------------------------------------
    def select_fn(split):
        train = df.iloc[split.train_slice()]
        scored = [(backtest(train, prm, cost).total_profit, prm) for prm in GRID]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def score_fn(split, params):
        test = df.iloc[split.test_slice()]
        r = backtest(test, params, cost)
        return FoldResult(split=split, net_profit=r.total_profit,
                          trades=r.n_trades,
                          returns=[t.profit for t in r.trades])

    report = run_walk_forward(n, select_fn, score_fn, n_folds=args.folds,
                              train_frac=args.train_frac, embargo=args.embargo,
                              n_trials=len(GRID), trial_sharpes=trial_sharpes)

    print("=" * 72)
    print("WALK-FORWARD  (parameters chosen on train, scored on unseen test)")
    print("=" * 72)
    for f in report.folds:
        s = f.split
        print(f"  fold {s.index}: train[{s.train_start}:{s.train_end}] "
              f"test[{s.test_start}:{s.test_end}]  "
              f"net={f.net_profit:+9.2f}  trades={f.trades:4d}  {f.chosen_params}")

    metrics = report.metrics()
    verdict = report.verdict()
    print("\n  metrics:")
    for k, v in metrics.items():
        print(f"    {k:<24}{v}")

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    print(verdict.explain())

    gap = best_profit - report.net_profit
    print(f"\nSelection bias: in-sample best {best_profit:+.2f} vs "
          f"out-of-sample {report.net_profit:+.2f}  (gap {gap:+.2f})")

    if args.json_out:
        payload = {
            "csv": args.csv, "bars": n, "cost_preset": args.cost,
            "grid_size": len(GRID),
            "in_sample_best": {"params": best_params, "net_profit": best_profit,
                               "profit_factor": best_result.profit_factor,
                               "trades": best_result.n_trades},
            "walk_forward": metrics,
            "folds": [{"index": f.split.index, "net_profit": f.net_profit,
                       "trades": f.trades, "params": f.chosen_params}
                      for f in report.folds],
            "verdict": {"passed": verdict.passed,
                        "failed_gates": list(verdict.failed_gates)},
            "selection_bias_gap": round(gap, 2),
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
        print(f"\nWrote {args.json_out}")

    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
