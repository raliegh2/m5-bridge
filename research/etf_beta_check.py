"""Is the ETF result an edge, or is it just beta?

Equity ETFs rise over long horizons. A strategy that buys dips in a persistently
rising asset will show a fine profit factor while doing nothing a buy-and-hold
investor could not do better and cheaper. Two tests separate the cases:

1. **Long/short split.** A real mean-reversion edge should make money fading
   moves in BOTH directions. If essentially all the profit comes from the long
   side, the strategy is harvesting drift, not reversion.
2. **Versus buy-and-hold.** The honest benchmark is not zero. It is holding the
   thing. A strategy returning 16% over 22 years on an asset that returned
   several hundred percent has subtracted value, whatever its profit factor.

    python research/etf_beta_check.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v16 import locked_config_v16, replay_v16  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.enums import Signal  # noqa: E402
from mt5_ai_bridge.instruments import cost_for, instrument_for  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"
START = 10_000.0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+",
                   default=["ONEQ", "IVV", "IWM", "VTI", "TQQQ", "EEM"])
    p.add_argument("--cost", default="tight")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cfg = locked_config_v16()
    print("=" * 92)
    print("IS THE ETF RESULT AN EDGE, OR BETA?")
    print("=" * 92)
    print("A dip-buying rule in a rising asset profits without adding "
          "anything.\n")

    head = (f"{'symbol':<7}{'years':>7}{'trades':>8}{'long P&L':>11}"
            f"{'short P&L':>11}{'long %':>8}{'strategy':>11}"
            f"{'buy&hold':>11}{'verdict':>14}")
    print(head)
    print("-" * len(head))

    rows = []
    for symbol in args.symbols:
        path = DATA_DIR / f"{symbol}_D1.csv"
        if not path.exists():
            continue
        audit = audit_bars(pd.read_csv(path), symbol, "D1")
        if not audit.usable:
            continue
        bars = load_csv(str(path)).reset_index(drop=True)
        if audit.trusted_from:
            bars = bars[bars["time"] >= audit.trusted_from].reset_index(drop=True)
        inst = instrument_for(symbol)
        cost = cost_for(symbol, args.cost)

        result = replay_v16(bars, cfg, cost, START, instrument=inst)
        if not result.trades:
            continue

        longs = [t for t in result.trades if t.side is Signal.BUY]
        shorts = [t for t in result.trades if t.side is Signal.SELL]
        long_pnl = sum(t.profit for t in longs)
        short_pnl = sum(t.profit for t in shorts)
        total = long_pnl + short_pnl

        years = (bars["time"].iloc[-1] - bars["time"].iloc[0]) / (365.25 * 86400)
        strategy_pct = result.net_profit / START * 100.0
        bh_pct = (float(bars["close"].iloc[-1]) / float(bars["close"].iloc[0])
                  - 1.0) * 100.0

        long_share = (long_pnl / total * 100.0) if total else 0.0
        if short_pnl <= 0 and long_pnl > 0:
            verdict = "BETA"
        elif strategy_pct < bh_pct:
            verdict = "beats nothing"
        else:
            verdict = "beats hold"
        rows.append({
            "symbol": symbol, "years": round(years, 1),
            "trades": len(result.trades), "longs": len(longs),
            "shorts": len(shorts),
            "long_pnl": round(long_pnl, 2), "short_pnl": round(short_pnl, 2),
            "long_share_pct": round(long_share, 1),
            "strategy_return_pct": round(strategy_pct, 2),
            "buy_hold_return_pct": round(bh_pct, 2),
            "verdict": verdict,
        })
        print(f"{symbol:<7}{years:>7.1f}{len(result.trades):>8}"
              f"{long_pnl:>11.2f}{short_pnl:>11.2f}{long_share:>7.0f}%"
              f"{strategy_pct:>10.1f}%{bh_pct:>10.1f}%{verdict:>14}")

    if not rows:
        print("Nothing to score.")
        return 2

    print("\n" + "=" * 92)
    print("READING")
    print("=" * 92)
    beta = [r for r in rows if r["short_pnl"] <= 0]
    beaten = [r for r in rows if r["strategy_return_pct"]
              < r["buy_hold_return_pct"]]
    print(f"  Symbols where the SHORT side lost money : {len(beta)}/{len(rows)}")
    print("    A genuine reversion edge should fade moves in both directions.")
    print(f"  Symbols beaten by simply holding        : "
          f"{len(beaten)}/{len(rows)}")
    print("    Buy-and-hold is the benchmark for a long-biased equity "
          "strategy, not zero.")

    if len(beta) >= len(rows) / 2 or len(beaten) >= len(rows) / 2:
        print("\n  VERDICT: this is largely equity drift, not a reversion "
              "edge. The profit\n  factor is real but it is being earned by "
              "being long a rising asset, which\n  a buy-and-hold investor "
              "does better with one trade and no spread.")
    else:
        print("\n  VERDICT: the result survives both checks and is worth "
              "treating seriously.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
