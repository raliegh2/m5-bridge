"""Run the LOCKED V15 candidate once, and report whatever comes out.

The discipline this script enforces:

* parameters come from ``research/v15_locked_candidate.json`` and are checked
  against the code's ``LOCKED`` config -- a mismatch aborts;
* nothing is searched, so the deflation trial count is 1;
* the acceptance gates are the ones written in the lock file, applied as-is;
* the result is printed whether it passes or fails.

Run:
    python research/v15_forward_test.py --csv GBPUSD_M5.csv
    python research/v15_forward_test.py --csv GBPUSD_M5.csv --cost wide
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_ai_bridge.candidate_v15 import (LOCK_PATH, locked_config,  # noqa: E402
                                         replay, resample_ohlc)
from mt5_ai_bridge.costs import preset  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.validation import (FoldResult, WalkForwardReport,  # noqa: E402
                                      evaluate, walk_forward_splits)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", default="GBPUSD_M5.csv")
    p.add_argument("--cost", default="typical")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cfg = locked_config()
    cost = preset(args.cost)

    print(f"Locked candidate : {LOCK_PATH.name}")
    print(f"Parameters       : H{cfg.timeframe_minutes // 60} "
          f"Donchian {cfg.entry_lookback}/{cfg.exit_lookback}, "
          f"{cfg.atr_stop_mult}x ATR({cfg.atr_period}) stop, "
          f"EMA{cfg.trend_ema} filter")
    print(f"Cost             : {args.cost} "
          f"({cost.round_trip_pips:.2f} pips round trip)")

    raw = load_csv(args.csv)
    if "time" not in raw.columns:
        print("ERROR: the CSV has no usable time column.")
        return 2
    bars = resample_ohlc(raw, cfg.timeframe_minutes)
    print(f"Data             : {len(raw)} M5 bars -> {len(bars)} "
          f"H{cfg.timeframe_minutes // 60} bars\n")

    # --- single pass over everything ---------------------------------------
    whole = replay(bars, cfg, cost)
    print("=" * 68)
    print("FULL-SAMPLE RESULT (single locked specification, nothing searched)")
    print("=" * 68)
    for k, v in whole.summary().items():
        print(f"  {k:<16}{v}")

    # --- walk-forward, purely for stability ---------------------------------
    # There is nothing to select, so each fold just replays the locked rules on
    # its own test slice. This measures consistency, not parameter fit.
    folds = []
    try:
        splits = walk_forward_splits(len(bars), n_folds=args.folds,
                                     train_frac=0.6,
                                     embargo=cfg.entry_lookback)
        for split in splits:
            slice_ = bars.iloc[split.test_slice()]
            r = replay(slice_, cfg, cost)
            folds.append(FoldResult(split=split, net_profit=r.net_profit,
                                    trades=len(r.trades), returns=r.returns))
    except ValueError as exc:
        print(f"\nWalk-forward not possible: {exc}")

    report = WalkForwardReport(folds=folds, n_trials=1,
                               trial_sharpes=[0.0, 0.0])
    metrics = report.metrics()

    print("\n" + "=" * 68)
    print("WALK-FORWARD CONSISTENCY (same locked rules on each unseen slice)")
    print("=" * 68)
    for f in report.folds:
        print(f"  fold {f.split.index}: bars[{f.split.test_start}:"
              f"{f.split.test_end}]  net={f.net_profit:+9.2f}  "
              f"trades={f.trades:3d}")
    for k, v in metrics.items():
        print(f"  {k:<24}{v}")

    verdict = evaluate(metrics)
    print("\n" + "=" * 68)
    print("VERDICT AGAINST THE LOCKED GATES")
    print("=" * 68)
    print(verdict.explain())

    if metrics["trades"] < 200:
        print("\nREAD THIS BEFORE INTERPRETING THE ABOVE:")
        print(f"  Only {metrics['trades']} out-of-sample trades were produced. "
              "That is far below the")
        print("  200-trade gate, so neither a pass nor a fail here is "
              "statistically meaningful.")
        print("  The binding constraint is DATA, not the strategy: this repo "
              "contains ~8 months")
        print("  of M5 GBPUSD and nothing else. Export several years of H4 "
              "history before")
        print("  drawing any conclusion about this candidate.")

    if args.json_out:
        payload = {
            "candidate": "V15_TIME_SERIES_MOMENTUM",
            "cost_preset": args.cost,
            "bars": len(bars),
            "full_sample": whole.summary(),
            "walk_forward": metrics,
            "verdict": {"passed": verdict.passed,
                        "failed_gates": list(verdict.failed_gates)},
            "statistically_conclusive": metrics["trades"] >= 200,
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
        print(f"\nWrote {args.json_out}")

    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
