"""The full per-symbol backtest table for every candidate, on audited data.

One place to see what each model does on each symbol, so the numbers cannot
drift apart across half a dozen JSON files. Everything here is computed on
audited, post-inception bars with quote-currency conversion applied.

    python research/backtest_report.py
    python research/backtest_report.py --timeframe D1 --cost tight
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v15 import locked_config, replay  # noqa: E402
from mt5_ai_bridge.candidate_v16 import locked_config_v16, replay_v16  # noqa: E402
from mt5_ai_bridge.costs import ZERO_COST, preset  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import (CONVERSION_SERIES, Converter,  # noqa: E402
                                       cost_for, instrument_for,
                                       quote_currency_of)
from mt5_ai_bridge.persistence import log_returns, variance_ratio  # noqa: E402
from mt5_ai_bridge.validation import (FoldResult, WalkForwardReport,  # noqa: E402
                                      walk_forward_splits)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"


def build_converters(timeframe: str) -> dict:
    out = {}
    for quote, series in CONVERSION_SERIES.items():
        path = DATA_DIR / f"{series}_{timeframe}.csv"
        if not path.exists():
            continue
        audit = audit_bars(pd.read_csv(path), series, timeframe)
        df = load_csv(str(path))
        if audit.trusted_from:
            df = df[df["time"] >= audit.trusted_from]
        if not df.empty:
            out[quote] = Converter.from_frame(df, series)
    return out


def walk_forward(bars, replay_fn, cfg, cost, instrument, folds, embargo):
    splits = walk_forward_splits(len(bars), n_folds=folds, train_frac=0.6,
                                 embargo=embargo)
    out = []
    for split in splits:
        r = replay_fn(bars.iloc[split.test_slice()].reset_index(drop=True),
                      cfg, cost, instrument=instrument)
        out.append(FoldResult(split=split, net_profit=r.net_profit,
                              trades=len(r.trades), returns=r.returns))
    return WalkForwardReport(folds=out, n_trials=1)


def run_candidate(name, replay_fn, cfg, embargo, bars_by_symbol, instruments,
                  tier, folds):
    rows = {}
    for symbol, bars in bars_by_symbol.items():
        inst = instruments[symbol]
        # Each symbol pays its OWN spread, in its own pips.
        cost = cost_for(symbol, tier)
        gross = replay_fn(bars, cfg, ZERO_COST, instrument=inst)
        net = replay_fn(bars, cfg, cost, instrument=inst)
        wf = walk_forward(bars, replay_fn, cfg, cost, inst, folds, embargo)
        wins = sum(1 for t in net.trades if t.profit > 0)
        rows[symbol] = {
            "trades": len(net.trades),
            "win_rate": round(wins / len(net.trades), 3) if net.trades else 0.0,
            "gross": gross.net_profit,
            "costs": net.total_costs,
            "net": net.net_profit,
            "net_pf": net.profit_factor,
            "oos_trades": wf.trades,
            "oos_net": wf.net_profit,
            "oos_pf": wf.profit_factor,
            "folds_pos": wf.positive_fold_fraction,
        }
    return name, rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--cost", default="typical")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    v15, v16 = locked_config(), locked_config_v16()
    converters = build_converters(args.timeframe)

    # --- load audited data ------------------------------------------------
    bars_by_symbol, instruments, meta = {}, {}, {}
    for path in sorted(DATA_DIR.glob(f"*_{args.timeframe}.csv")):
        symbol = path.stem.rsplit("_", 1)[0]
        try:
            inst = instrument_for(symbol,
                                  converters.get(quote_currency_of(symbol)))
        except ValueError:
            continue
        audit = audit_bars(pd.read_csv(path), symbol, args.timeframe)
        if not audit.usable:
            continue
        df = load_csv(str(path)).reset_index(drop=True)
        dropped = 0
        if audit.trusted_from:
            before = len(df)
            df = df[df["time"] >= audit.trusted_from].reset_index(drop=True)
            dropped = before - len(df)
        if len(df) < 500:
            continue
        bars_by_symbol[symbol] = df
        instruments[symbol] = inst
        try:
            vr = variance_ratio(log_returns(df["close"].tolist()), q=30)
            vr_txt = f"{vr.ratio:.3f}{'*' if vr.p_value < 0.05 else ''}"
        except ValueError:
            vr_txt = "-"
        meta[symbol] = {"from": audit.trusted_from_date, "bars": len(df),
                        "dropped": dropped, "vr30": vr_txt,
                        "quote": quote_currency_of(symbol),
                        "pip_value": round(inst.pip_value_per_lot, 2)}

    if not bars_by_symbol:
        print(f"No usable {args.timeframe} data in {DATA_DIR}.")
        return 2

    print("=" * 96)
    print(f"BACKTEST REPORT  |  {args.timeframe}  |  cost tier={args.cost}"
          f"  |  {args.folds} walk-forward folds")
    print("=" * 96)

    print("\nDATA (audited, post-inception only) and per-symbol costs")
    print(f"{'symbol':<9}{'quote':<7}{'from':<13}{'bars':>8}{'dropped':>9}"
          f"{'VR q=30':>10}{'spread':>10}{'$/pip/lot':>11}")
    print("-" * 77)
    for s, m in meta.items():
        c = cost_for(s, args.cost)
        print(f"{s:<9}{m['quote']:<7}{m['from']:<13}{m['bars']:>8}"
              f"{m['dropped']:>9}{m['vr30']:>10}"
              f"{c.spread_pips:>9.1f}p{m['pip_value']:>11.2f}")
    print("  VR > 1 trends, < 1 mean-reverts, * = significant at 5%")
    print("  Spread is in EACH symbol's own pips: 0.9 on EURUSD, 30 on gold "
          "(a gold pip is 1 cent).")

    results = {}
    for name, replay_fn, cfg, embargo in (
            ("V15 momentum (Donchian 20/10 breakout)", replay, v15,
             v15.entry_lookback),
            ("V16 mean reversion (2 sigma fade)", replay_v16, v16,
             v16.lookback)):
        _, rows = run_candidate(name, replay_fn, cfg, embargo,
                                bars_by_symbol, instruments, args.cost,
                                args.folds)
        results[name] = rows

        print(f"\n{name.upper()}")
        head = (f"{'symbol':<9}{'trades':>7}{'win%':>7}{'gross':>12}"
                f"{'costs':>11}{'net':>12}{'net PF':>8}"
                f"{'OOS net':>12}{'OOS PF':>8}{'folds+':>8}")
        print(head)
        print("-" * len(head))
        for s, r in rows.items():
            print(f"{s:<9}{r['trades']:>7}{r['win_rate'] * 100:>6.1f}%"
                  f"{r['gross']:>12.2f}{r['costs']:>11.2f}{r['net']:>12.2f}"
                  f"{r['net_pf']:>8.3f}{r['oos_net']:>12.2f}"
                  f"{r['oos_pf']:>8.3f}{r['folds_pos']:>7.0%}")
        tg = sum(r["gross"] for r in rows.values())
        tn = sum(r["net"] for r in rows.values())
        tc = sum(r["costs"] for r in rows.values())
        pos = [s for s, r in rows.items() if r["oos_net"] > 0]
        print("-" * len(head))
        print(f"{'TOTAL':<9}{sum(r['trades'] for r in rows.values()):>7}"
              f"{'':>7}{tg:>12.2f}{tc:>11.2f}{tn:>12.2f}")
        print(f"  Profitable out of sample: {len(pos)}/{len(rows)}"
              + (f" ({', '.join(pos)})" if pos else ""))
        gross_pos = [s for s, r in rows.items() if r["gross"] > 0]
        print(f"  Profitable GROSS        : {len(gross_pos)}/{len(rows)}"
              + (f" ({', '.join(gross_pos)})" if gross_pos else ""))

    # --- the comparison that matters --------------------------------------
    print("\n" + "=" * 96)
    print("GROSS vs NET  -- where an edge exists but costs consume it")
    print("=" * 96)
    print(f"{'symbol':<9}{'model':<22}{'gross':>12}{'costs':>11}{'net':>12}"
          f"{'cost/trade':>12}{'verdict':>26}")
    print("-" * 104)
    for name, rows in results.items():
        short = "V15 momentum" if "V15" in name else "V16 reversion"
        for s, r in rows.items():
            per = r["costs"] / r["trades"] if r["trades"] else 0.0
            if r["gross"] > 0 and r["net"] <= 0:
                verdict = "EDGE EATEN BY COSTS"
            elif r["gross"] > 0:
                verdict = "profitable net"
            else:
                verdict = "no gross edge"
            print(f"{s:<9}{short:<22}{r['gross']:>12.2f}{r['costs']:>11.2f}"
                  f"{r['net']:>12.2f}{per:>12.2f}{verdict:>26}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"timeframe": args.timeframe, "cost": args.cost,
             "data": meta, "results": results}, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
