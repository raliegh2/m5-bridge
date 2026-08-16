"""Run the LOCKED V16 mean-reversion candidate once, across audited symbols.

V16 is not a fresh start. It is another specification tried against the same
data, so it is deflated against **every trial on record** in
research/v15_trials.json, not against one. That is the difference between an
honest test and a twenty-sixth report.

Data is trimmed automatically to each symbol's audited trusted window
(tools/audit_history.py), so no result here rests on synthetic history.

    python research/v16_forward_test.py
    python research/v16_forward_test.py --cost wide
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v16 import locked_config_v16, replay_v16  # noqa: E402
from mt5_ai_bridge.costs import ZERO_COST, preset  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import instrument_for, quote_currency_of  # noqa: E402
from mt5_ai_bridge.validation import (FoldResult, TrialRegistry,  # noqa: E402
                                      WalkForwardReport,
                                      deflated_sharpe_ratio, evaluate,
                                      sharpe_ratio, walk_forward_splits)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"
REGISTRY = Path(__file__).resolve().parents[1] / "research" / "v15_trials.json"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--cost", default="typical")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cfg = locked_config_v16()
    cost = preset(args.cost)

    print(f"Locked candidate : v16_locked_candidate.json")
    print(f"Rules            : fade a {cfg.entry_z} sigma stretch from a "
          f"{cfg.lookback}-bar mean, exit at {cfg.exit_z} sigma, "
          f"stop {cfg.stop_z} sigma, time stop {cfg.max_holding_bars} bars")
    print(f"Cost             : {args.cost} "
          f"({cost.round_trip_pips:.2f}p round trip)\n")

    registry = TrialRegistry(REGISTRY)
    before = registry.count

    header = (f"{'symbol':<9}{'from':<12}{'bars':>7}{'trades':>8}"
              f"{'gross':>11}{'net':>11}{'net PF':>8}{'OOS net':>11}{'folds+':>8}")
    print(header)
    print("-" * len(header))

    results, oos_sharpes, skipped = {}, [], {}
    for path in sorted(DATA_DIR.glob(f"*_{args.timeframe}.csv")):
        symbol = path.stem.rsplit("_", 1)[0]
        try:
            # Same refusal discipline: JPY pairs need a converter, and V16's
            # per-symbol replay does not carry one, so they are skipped here.
            if quote_currency_of(symbol) != "USD":
                raise ValueError("needs a quote-currency converter")
            instrument = instrument_for(symbol)
        except ValueError as exc:
            skipped[symbol] = str(exc).split(";")[0]
            continue

        audit = audit_bars(pd.read_csv(path), symbol, args.timeframe)
        if not audit.usable:
            skipped[symbol] = "failed audit"
            continue

        bars = load_csv(str(path)).reset_index(drop=True)
        if audit.trusted_from:
            bars = bars[bars["time"] >= audit.trusted_from].reset_index(drop=True)
        if len(bars) < 500:
            skipped[symbol] = "too few trusted bars"
            continue

        registry.record({"candidate": "V16", "symbol": symbol,
                         "timeframe": args.timeframe}, label="v16")

        gross = replay_v16(bars, cfg, ZERO_COST, instrument=instrument)
        net = replay_v16(bars, cfg, cost, instrument=instrument)

        splits = walk_forward_splits(len(bars), n_folds=args.folds,
                                     train_frac=0.6, embargo=cfg.lookback)
        folds = []
        for split in splits:
            r = replay_v16(bars.iloc[split.test_slice()].reset_index(drop=True),
                           cfg, cost, instrument=instrument)
            folds.append(FoldResult(split=split, net_profit=r.net_profit,
                                    trades=len(r.trades), returns=r.returns))
        report = WalkForwardReport(folds=folds, n_trials=registry.count)
        oos_sharpes.append(sharpe_ratio(report.all_returns, 1.0))

        results[symbol] = {
            "trusted_from": audit.trusted_from_date,
            "bars": len(bars), "trades": len(net.trades),
            "gross": gross.net_profit, "net": net.net_profit,
            "net_pf": net.profit_factor,
            "oos_net": report.net_profit, "oos_trades": report.trades,
            "oos_pf": report.profit_factor,
            "folds_positive": report.positive_fold_fraction,
            "_returns": report.all_returns,
        }
        r = results[symbol]
        print(f"{symbol:<9}{r['trusted_from']:<12}{r['bars']:>7}"
              f"{r['trades']:>8}{r['gross']:>11.2f}{r['net']:>11.2f}"
              f"{r['net_pf']:>8.3f}{r['oos_net']:>11.2f}"
              f"{r['folds_positive']:>7.0%}")

    if skipped:
        print(f"\nSkipped: {'; '.join(f'{k} ({v})' for k, v in skipped.items())}")
    if not results:
        print("\nNo symbols could be tested.")
        return 2

    n_trials = registry.count
    best = max(results, key=lambda s: results[s]["oos_net"])
    b = results[best]
    dsr = deflated_sharpe_ratio(b["_returns"], n_trials=n_trials,
                                trial_sharpes=oos_sharpes)
    metrics = {
        "trades": b["oos_trades"], "net_profit": b["oos_net"],
        "profit_factor": b["oos_pf"],
        "positive_fold_fraction": b["folds_positive"],
        "deflated_sharpe": round(dsr, 4),
    }
    verdict = evaluate(metrics)

    print(f"\nTrials on record: {before} before this run, {n_trials} after.")
    print(f"Best symbol out of sample: {best}")
    for k, v in metrics.items():
        print(f"  {k:<24}{v}")

    print("\n" + "=" * 62)
    print("V16 VERDICT (deflated by every trial on record)")
    print("=" * 62)
    print(verdict.explain())

    profitable = [s for s, r in results.items() if r["oos_net"] > 0]
    print(f"\nSymbols profitable out of sample: {len(profitable)}/{len(results)}"
          + (f"  ({', '.join(profitable)})" if profitable else ""))
    print("\nThe lock file predeclared FAIL as the expected outcome: no "
          "variance ratio\nwas individually significant, so the base rate for "
          "this working was low.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "candidate": "V16_RANGE_MEAN_REVERSION",
            "cost": args.cost, "n_trials": n_trials,
            "best_symbol": best, "metrics": metrics,
            "verdict": {"passed": verdict.passed,
                        "failed_gates": list(verdict.failed_gates)},
            "skipped": skipped,
            "by_symbol": {s: {k: v for k, v in r.items()
                              if not k.startswith("_")}
                          for s, r in results.items()},
        }, indent=2))
        print(f"\nWrote {args.json_out}")

    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
