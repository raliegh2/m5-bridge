"""Test V19 against its registered prediction, not just its profitability.

`research/v19_locked_candidate.json` predicts, before any result was computed:

    V19 should beat V16 on AUDUSD, EURUSD and USDJPY, and should NOT beat it
    on GBPUSD or GBPJPY.

That is the informative test. A filter that simply trades less will often look
better on some subset, so "V19 made money" proves nothing. A directional,
symbol-specific prediction can fail even when the strategy is profitable -- and
if V19 improves everywhere or nowhere, the volume split is noise regardless of
the P&L.

The gate verdict is reported too, and is expected to fail.

    python research/v19_forward_test.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v16 import locked_config_v16, replay_v16  # noqa: E402
from mt5_ai_bridge.candidate_v19 import locked_config_v19, replay_v19  # noqa: E402
from mt5_ai_bridge.costs import ZERO_COST  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import (Converter, cost_for,  # noqa: E402
                                       instrument_for, quote_currency_of)
from mt5_ai_bridge.validation import (FoldResult, TrialRegistry,  # noqa: E402
                                      WalkForwardReport, evaluate,
                                      sharpe_ratio, walk_forward_splits)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"
REGISTRY = Path(__file__).resolve().parents[1] / "research" / "v15_trials.json"

# Registered in the lock file before any V19 result existed.
PREDICTED_IMPROVE = ("AUDUSD", "EURUSD", "USDJPY")
PREDICTED_NOT_IMPROVE = ("GBPUSD", "GBPJPY")


def load(symbol: str, timeframe: str):
    path = DATA_DIR / f"{symbol}_{timeframe}.csv"
    if not path.exists():
        return None
    raw = pd.read_csv(path)
    if "tick_volume" not in raw.columns:
        return None
    audit = audit_bars(raw, symbol, timeframe)
    if not audit.usable:
        return None
    df = load_csv(str(path)).reset_index(drop=True)
    vol = raw[["time", "tick_volume"]]
    df = df.merge(vol, on="time", how="left")
    if audit.trusted_from:
        df = df[df["time"] >= audit.trusted_from].reset_index(drop=True)
    df = df[df["tick_volume"].fillna(0) > 0].reset_index(drop=True)
    return df if len(df) >= 2000 else None


def walk(bars, replay_fn, cfg, cost, inst, folds, embargo):
    splits = walk_forward_splits(len(bars), n_folds=folds, train_frac=0.6,
                                 embargo=embargo)
    out = []
    for split in splits:
        r = replay_fn(bars.iloc[split.test_slice()].reset_index(drop=True),
                      cfg, cost, instrument=inst)
        out.append(FoldResult(split=split, net_profit=r.net_profit,
                              trades=len(r.trades), returns=r.returns))
    return WalkForwardReport(folds=out, n_trials=1)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--cost", default="tight")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    v16 = locked_config_v16()
    v19 = locked_config_v19()

    print("=" * 92)
    print("V19: V16 REVERSION RESTRICTED TO BELOW-AVERAGE-VOLUME BARS")
    print("=" * 92)
    print(f"Filter    : relative volume < {v19.max_relative_volume} "
          f"over a {v19.volume_lookback}-bar trailing mean")
    print(f"Signal    : identical to V16 in every parameter")
    print(f"Cost      : per-instrument, tier '{args.cost}'")
    print(f"\nRegistered prediction: improve on "
          f"{', '.join(PREDICTED_IMPROVE)};")
    print(f"                      NOT improve on "
          f"{', '.join(PREDICTED_NOT_IMPROVE)}\n")

    head = (f"{'symbol':<9}{'V16 gross':>11}{'V19 gross':>11}{'V16 PF':>9}"
            f"{'V19 PF':>9}{'V16 trd':>9}{'V19 trd':>9}{'improved':>10}"
            f"{'predicted':>11}{'':>4}")
    print(head)
    print("-" * len(head))

    rows, hits, misses = {}, [], []
    registry = TrialRegistry(REGISTRY)
    before = registry.count

    # JPY-quoted pairs are half the registered prediction, so the converter is
    # built rather than skipping them.
    converters = {}
    jpy_path = DATA_DIR / f"USDJPY_{args.timeframe}.csv"
    if jpy_path.exists():
        jpy_audit = audit_bars(pd.read_csv(jpy_path), "USDJPY", args.timeframe)
        jpy = load_csv(str(jpy_path))
        if jpy_audit.trusted_from:
            jpy = jpy[jpy["time"] >= jpy_audit.trusted_from]
        if not jpy.empty:
            converters["JPY"] = Converter.from_frame(jpy, "USDJPY")

    for symbol in list(PREDICTED_IMPROVE) + list(PREDICTED_NOT_IMPROVE) + ["XAUUSD"]:
        bars = load(symbol, args.timeframe)
        if bars is None:
            continue
        try:
            inst = instrument_for(symbol,
                                  converters.get(quote_currency_of(symbol)))
        except ValueError as exc:
            print(f"{symbol:<9} skipped: {str(exc).split(';')[0]}")
            continue
        registry.record({"candidate": "V19", "symbol": symbol,
                         "timeframe": args.timeframe}, label="v19")
        cost = cost_for(symbol, args.cost)

        # Frictionless comparison isolates the filter from the cost of trading.
        g16 = walk(bars, replay_v16, v16, ZERO_COST, inst, args.folds,
                   v16.lookback)
        g19 = walk(bars, replay_v19, v19, ZERO_COST, inst, args.folds,
                   v19.lookback)
        n16 = walk(bars, replay_v16, v16, cost, inst, args.folds, v16.lookback)
        n19 = walk(bars, replay_v19, v19, cost, inst, args.folds, v19.lookback)

        improved = g19.profit_factor > g16.profit_factor
        predicted = symbol in PREDICTED_IMPROVE
        expected = symbol in PREDICTED_IMPROVE or symbol in PREDICTED_NOT_IMPROVE
        mark = ""
        if expected:
            if improved == predicted:
                hits.append(symbol)
                mark = "OK"
            else:
                misses.append(symbol)
                mark = "MISS"

        rows[symbol] = {
            "v16_gross": g16.net_profit, "v19_gross": g19.net_profit,
            "v16_gross_pf": g16.profit_factor, "v19_gross_pf": g19.profit_factor,
            "v16_trades": g16.trades, "v19_trades": g19.trades,
            "v16_net": n16.net_profit, "v19_net": n19.net_profit,
            "v19_net_pf": n19.profit_factor,
            "v19_folds_pos": n19.positive_fold_fraction,
            "improved_gross_pf": improved,
            "predicted_improve": predicted if expected else None,
            "_returns": n19.all_returns,
        }
        pred_txt = ("improve" if predicted else "no change") if expected else "-"
        print(f"{symbol:<9}{g16.net_profit:>11.2f}{g19.net_profit:>11.2f}"
              f"{g16.profit_factor:>9.3f}{g19.profit_factor:>9.3f}"
              f"{g16.trades:>9}{g19.trades:>9}"
              f"{('yes' if improved else 'no'):>10}{pred_txt:>11}{mark:>4}")

    if not rows:
        print("No symbols could be tested.")
        return 2

    # --- did the prediction hold? -----------------------------------------
    print("\n" + "=" * 92)
    print("DID THE REGISTERED PREDICTION HOLD?")
    print("=" * 92)
    total = len(hits) + len(misses)
    print(f"  Correct on {len(hits)}/{total} predicted symbols"
          + (f"  (hits: {', '.join(hits)})" if hits else ""))
    if misses:
        print(f"  Wrong on: {', '.join(misses)}")

    # A raw hit rate flatters the result. V19 trades less than V16, so it
    # rarely improves anything; predicting "no improvement" is nearly free.
    # Only the symbols predicted to IMPROVE carry information.
    improved_any = [s for s, r in rows.items() if r["improved_gross_pf"]]
    base_rate = len(improved_any) / len(rows) if rows else 0.0
    up_hits = [s for s in hits if s in PREDICTED_IMPROVE]
    print(f"\n  Base rate: V19 improved {len(improved_any)}/{len(rows)} "
          f"symbols overall ({base_rate:.0%}).")
    print(f"  Predicting 'no improvement' is therefore right ~"
          f"{1 - base_rate:.0%} of the time by default, so the two")
    print("  no-change hits carry little information. The informative "
          "count is:")
    print(f"    symbols predicted to IMPROVE that did: "
          f"{len(up_hits)}/{len(PREDICTED_IMPROVE)}"
          + (f"  ({', '.join(up_hits)})" if up_hits else ""))
    if len(up_hits) <= max(1, round(base_rate * len(PREDICTED_IMPROVE))):
        print("    That is at or below what chance would produce. The "
              "mechanism is NOT established.")

    eurusd = rows.get("EURUSD", {})
    gbpusd = rows.get("GBPUSD", {})
    falsified = []
    if gbpusd and gbpusd["improved_gross_pf"]:
        falsified.append("V19 improved GBPUSD, which the measurement said "
                         "should not happen")
    if eurusd and not eurusd["improved_gross_pf"]:
        falsified.append("V19 failed to improve EURUSD, the symbol with the "
                         "cleanest measured effect")

    if falsified:
        print("\n  FALSIFIED:")
        for f in falsified:
            print(f"    - {f}")
        print("\n  The volume split does not behave the way the "
              "autocorrelation measurement\n  said it would. Treat any V19 "
              "profit as coincidence.")
    elif len(hits) == total and total >= 4:
        print("\n  The prediction held across every symbol. The volume effect "
              "behaves as\n  Campbell-Grossman-Wang describes, which is "
              "genuine out-of-sample support\n  for the mechanism -- "
              "independent of whether the gates pass.")
    else:
        print("\n  Partially supported. Not strong enough to treat the "
              "mechanism as established.")

    # --- gate verdict on the best symbol ----------------------------------
    best = max(rows, key=lambda s: rows[s]["v19_net"])
    b = rows[best]
    n_trials = registry.count
    from mt5_ai_bridge.validation import deflated_sharpe_ratio
    dsr = deflated_sharpe_ratio(
        b["_returns"], n_trials=n_trials,
        trial_sharpes=[sharpe_ratio(r["_returns"], 1.0) for r in rows.values()])
    metrics = {
        "trades": b["v19_trades"], "net_profit": b["v19_net"],
        "profit_factor": b["v19_net_pf"],
        "positive_fold_fraction": b["v19_folds_pos"],
        "deflated_sharpe": round(dsr, 4),
    }
    verdict = evaluate(metrics)

    print("\n" + "=" * 92)
    print(f"GATE VERDICT (best symbol: {best}, {before} -> {n_trials} trials)")
    print("=" * 92)
    for k, v in metrics.items():
        print(f"  {k:<24}{v}")
    print()
    print(verdict.explain())

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "timeframe": args.timeframe, "cost": args.cost,
            "predicted_improve": list(PREDICTED_IMPROVE),
            "predicted_not_improve": list(PREDICTED_NOT_IMPROVE),
            "prediction_hits": hits, "prediction_misses": misses,
            "falsified": falsified,
            "n_trials": n_trials, "metrics": metrics,
            "verdict": {"passed": verdict.passed,
                        "failed_gates": list(verdict.failed_gates)},
            "by_symbol": {s: {k: v for k, v in r.items()
                              if not k.startswith("_")}
                          for s, r in rows.items()},
        }, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
