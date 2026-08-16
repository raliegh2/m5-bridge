"""Run the LOCKED V17 system once, walk-forward, and report whatever it gives.

Symbol admission is recomputed inside each fold from the training window only,
so a symbol is never traded on the strength of data it is about to be scored
on. Deflation counts every specification on record, not just this one.

    python research/v17_system_test.py
    python research/v17_system_test.py --cost tight
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import (CONVERSION_SERIES, Converter,  # noqa: E402
                                       cost_for, instrument_for,
                                       quote_currency_of)
from mt5_ai_bridge.portfolio_v15 import (PortfolioConfig,  # noqa: E402
                                         diversification_report)
from mt5_ai_bridge.system_v17 import (admit_by_persistence,  # noqa: E402
                                      locked_system, replay_system)
from mt5_ai_bridge.validation import (FoldResult, TrialRegistry,  # noqa: E402
                                      WalkForwardReport, evaluate,
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

    cfg = locked_system()
    pcfg = PortfolioConfig()

    print("=" * 84)
    print("V17 VR-GATED MEAN-REVERSION SYSTEM")
    print("=" * 84)
    print(f"Signal   : fade a {cfg.entry_z} sigma stretch from a "
          f"{cfg.lookback}-bar mean, exit {cfg.exit_z}, stop {cfg.stop_z}, "
          f"time stop {cfg.max_holding_bars} bars")
    print(f"Gate     : trade only symbols with variance ratio < {cfg.vr_max} "
          f"at q={cfg.vr_horizon}, measured per fold on training data only")
    print(f"Risk     : {pcfg.risk_percent_per_trade}%/trade, "
          f"{pcfg.max_total_risk_percent}% total, "
          f"{pcfg.max_currency_risk_percent}%/currency, "
          f"max {pcfg.max_concurrent_positions} concurrent")
    print(f"Costs    : tier '{args.cost}', per instrument\n")

    # --- converters -------------------------------------------------------
    converters = {}
    for quote, series in CONVERSION_SERIES.items():
        path = DATA_DIR / f"{series}_{args.timeframe}.csv"
        if not path.exists():
            continue
        audit = audit_bars(pd.read_csv(path), series, args.timeframe)
        df = load_csv(str(path))
        if audit.trusted_from:
            df = df[df["time"] >= audit.trusted_from]
        if not df.empty:
            converters[quote] = Converter.from_frame(df, series)

    # --- audited data -----------------------------------------------------
    bars, costs = {}, {}
    for path in sorted(DATA_DIR.glob(f"*_{args.timeframe}.csv")):
        symbol = path.stem.rsplit("_", 1)[0]
        try:
            instrument_for(symbol, converters.get(quote_currency_of(symbol)))
        except ValueError:
            continue
        audit = audit_bars(pd.read_csv(path), symbol, args.timeframe)
        if not audit.usable:
            continue
        df = load_csv(str(path)).reset_index(drop=True)
        if audit.trusted_from:
            df = df[df["time"] >= audit.trusted_from].reset_index(drop=True)
        if len(df) < 1000:
            continue
        bars[symbol] = df
        costs[symbol] = cost_for(symbol, args.cost)

    if not bars:
        print("No usable data.")
        return 2

    div = diversification_report(bars)
    print(f"Universe : {len(bars)} symbols, {div['effective_bets']} effective "
          f"bets (mean |rho| {div['mean_abs_correlation']})\n")

    registry = TrialRegistry(REGISTRY)
    before = registry.count
    registry.record({"system": "V17", "timeframe": args.timeframe,
                     "entry_z": cfg.entry_z, "vr_max": cfg.vr_max},
                    label="v17")

    # --- walk-forward with per-fold admission ------------------------------
    n_bars = min(len(df) for df in bars.values())
    splits = walk_forward_splits(n_bars, n_folds=args.folds, train_frac=0.6,
                                 embargo=cfg.lookback)

    folds, admission_log = [], {}
    for split in splits:
        admitted, detail = admit_by_persistence(bars, cfg,
                                                upto=split.train_end)
        admission_log[split.index] = {"admitted": admitted, "detail": detail}
        if not admitted:
            folds.append(FoldResult(split=split, net_profit=0.0, trades=0,
                                    returns=[]))
            print(f"  fold {split.index}: NO SYMBOLS PASS THE VR GATE")
            continue

        slice_ = {s: bars[s].iloc[split.test_slice()].reset_index(drop=True)
                  for s in admitted}
        result = replay_system(slice_, cfg, pcfg, costs,
                               admitted=admitted, converters=converters)
        folds.append(FoldResult(split=split, net_profit=result.net_profit,
                                trades=len(result.trades),
                                returns=result.returns))
        vrs = ", ".join(f"{s}:{detail[s]['vr']:.3f}" for s in admitted)
        print(f"  fold {split.index}: net={result.net_profit:+10.2f}  "
              f"trades={len(result.trades):4d}  "
              f"dd={result.max_drawdown_percent:5.2f}%  admitted=[{vrs}]")

    n_trials = registry.count
    report = WalkForwardReport(
        folds=folds, n_trials=n_trials,
        trial_sharpes=[sharpe_ratio(f.returns, 1.0) for f in folds if f.returns])
    metrics = report.metrics()
    verdict = report.verdict()

    print("\n  metrics:")
    for k, v in metrics.items():
        print(f"    {k:<24}{v}")
    print(f"    {'trials before this run':<24}{before}")

    print("\n" + "=" * 84)
    print("V17 VERDICT")
    print("=" * 84)
    print(verdict.explain())

    if metrics["trades"] < 200:
        print(f"\nNOTE: {metrics['trades']} out-of-sample trades. The lock file "
              "predeclared this risk:\n  a 3-sigma entry is far rarer than a "
              "2-sigma one, so the frequency cut that\n  fixes the cost problem "
              "can itself starve the sample.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "system": "V17_VR_GATED_REVERSION", "timeframe": args.timeframe,
            "cost": args.cost, "n_trials": n_trials,
            "universe": list(bars), "diversification": div,
            "metrics": metrics,
            "verdict": {"passed": verdict.passed,
                        "failed_gates": list(verdict.failed_gates)},
            "folds": [{"index": f.split.index, "net_profit": f.net_profit,
                       "trades": f.trades} for f in folds],
            "admission": {str(k): v["admitted"]
                          for k, v in admission_log.items()},
        }, indent=2))
        print(f"\nWrote {args.json_out}")

    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
