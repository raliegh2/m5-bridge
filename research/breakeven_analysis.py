"""What would it cost to make this signal viable, and is that cost obtainable?

Every previous run asked "is it profitable at cost X?". This asks the decisive
question the other way round: **what is the maximum all-in cost per trade at
which the signal clears each gate**, and how does that compare to what a broker
will actually charge?

That turns viability from an opinion into an inequality. Two numbers settle it:

* the **frictionless profit factor** -- what the signal earns with no costs at
  all. If this is below the required gate, no broker on earth makes it viable,
  because cost can only subtract.
* the **break-even cost per trade** -- gross profit divided by trade count. Any
  charge above this loses money.

Realistic retail pricing is included for comparison. Note that spread-based and
commission-based accounts land in a similar place: an ECN at 0.2 pips raw plus
$7/lot round turn is ~0.2 + 0.7 = ~0.9 pips all-in, the same as a 0.9-pip
spread account with no commission. Choosing a broker moves this less than
people expect.

    python research/breakeven_analysis.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v16 import locked_config_v16, replay_v16  # noqa: E402
from mt5_ai_bridge.costs import ZERO_COST  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import instrument_for  # noqa: E402
from mt5_ai_bridge.validation import (FoldResult, WalkForwardReport,  # noqa: E402
                                      walk_forward_splits)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"

GATE_PROFIT_FACTOR = 1.10

# All-in round-trip cost in pips for realistic retail arrangements. Commission
# is converted at $10 per pip per standard lot.
BROKER_PRICING = {
    "ECN raw + $7/lot": 0.2 + 0.7,
    "ECN raw + $5/lot": 0.2 + 0.5,
    "tight spread account": 0.6,
    "typical spread account": 0.9,
    "wide / retail market maker": 1.8,
    "institutional (indicative)": 0.15,
}


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
    p.add_argument("--timeframes", nargs="+", default=["H4", "H1", "D1"])
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cfg = locked_config_v16()
    print("=" * 86)
    print("BREAK-EVEN COST ANALYSIS -- V16 signal, frictionless")
    print("=" * 86)
    print("If the frictionless profit factor is below the gate, no broker can "
          "make it viable.\n")

    head = (f"{'tf':<4}{'symbol':<8}{'OOS trades':>11}{'gross OOS':>12}"
            f"{'gross PF':>10}{'gate PF':>9}{'$/trade':>10}"
            f"{'max cost (pips)':>17}")
    print(head)
    print("-" * len(head))

    rows = []
    for timeframe in args.timeframes:
        for symbol in args.symbols:
            bars = load(symbol, timeframe)
            if bars is None:
                continue
            inst = instrument_for(symbol)
            splits = walk_forward_splits(len(bars), n_folds=args.folds,
                                         train_frac=0.6, embargo=cfg.lookback)
            folds = []
            for split in splits:
                r = replay_v16(
                    bars.iloc[split.test_slice()].reset_index(drop=True),
                    cfg, ZERO_COST, instrument=inst)
                folds.append(FoldResult(split=split, net_profit=r.net_profit,
                                        trades=len(r.trades),
                                        returns=r.returns))
            wf = WalkForwardReport(folds=folds, n_trials=1)
            trades = wf.trades
            if trades == 0:
                continue

            gross = wf.net_profit
            pf = wf.profit_factor
            per_trade = gross / trades

            # Average lots actually used, so cost in pips converts to dollars.
            full = replay_v16(bars, cfg, ZERO_COST, instrument=inst)
            avg_lots = (sum(t.lots for t in full.trades) / len(full.trades)
                        if full.trades else 0.0)
            pip_value = inst.pip_value_per_lot
            max_cost_pips = (per_trade / (avg_lots * pip_value)
                             if avg_lots > 0 and pip_value > 0 else 0.0)

            rows.append({
                "timeframe": timeframe, "symbol": symbol,
                "oos_trades": trades, "gross_oos": round(gross, 2),
                "gross_pf": pf, "per_trade": round(per_trade, 3),
                "avg_lots": round(avg_lots, 3),
                "max_cost_pips": round(max_cost_pips, 3),
                "clears_gate_frictionless": pf >= GATE_PROFIT_FACTOR,
            })
            flag = "" if pf >= GATE_PROFIT_FACTOR else "  <- below gate"
            print(f"{timeframe:<4}{symbol:<8}{trades:>11}{gross:>12.2f}"
                  f"{pf:>10.3f}{GATE_PROFIT_FACTOR:>9.2f}{per_trade:>10.3f}"
                  f"{max_cost_pips:>17.3f}{flag}")

    if not rows:
        print("No usable data.")
        return 2

    # --- the decisive test ------------------------------------------------
    best = max(rows, key=lambda r: r["gross_pf"])
    clears = [r for r in rows if r["clears_gate_frictionless"]]

    print("\n" + "=" * 86)
    print("THE DECISIVE TEST")
    print("=" * 86)
    print(f"  Best frictionless profit factor anywhere : "
          f"{best['gross_pf']:.3f}  ({best['timeframe']} {best['symbol']})")
    print(f"  Profit factor required by the gate       : "
          f"{GATE_PROFIT_FACTOR:.3f}")
    print(f"  Configurations clearing it at ZERO cost  : "
          f"{len(clears)}/{len(rows)}")

    if not clears:
        print("\n  No configuration reaches the required profit factor even "
              "with zero costs.")
        print("  Cost can only subtract, so this is not a broker problem and "
              "cannot be")
        print("  solved by choosing a cheaper account. The raw signal is too "
              "weak.")
    else:
        print("\n  Configurations clearing the gate frictionless:")
        for r in clears:
            print(f"    {r['timeframe']} {r['symbol']}: PF {r['gross_pf']:.3f}, "
                  f"max affordable cost {r['max_cost_pips']:.3f} pips/trade")

    # --- what brokers charge ---------------------------------------------
    print("\n" + "=" * 86)
    print("WHAT IS ACTUALLY OBTAINABLE (all-in round trip, pips)")
    print("=" * 86)
    for name, pips in sorted(BROKER_PRICING.items(), key=lambda kv: kv[1]):
        affordable = [r for r in rows if r["max_cost_pips"] >= pips]
        note = (f"{len(affordable)} config(s) still profitable"
                if affordable else "nothing profitable")
        print(f"  {name:<28}{pips:>6.2f}   {note}")
    print("\n  An ECN at 0.2 pips raw plus $7/lot is ~0.9 pips all in -- the "
          "same place a\n  0.9-pip spread account lands. Broker choice moves "
          "this less than expected.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "gate_profit_factor": GATE_PROFIT_FACTOR,
            "best_frictionless_pf": best["gross_pf"],
            "configs_clearing_frictionless": len(clears),
            "broker_pricing_pips": BROKER_PRICING,
            "rows": rows,
        }, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
