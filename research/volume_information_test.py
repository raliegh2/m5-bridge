"""Does tick volume tell us anything price does not?

Tested before any strategy is built on it, which is the discipline that let the
variance ratio predict V15's failure in advance.

The hypothesis is Campbell, Grossman & Wang (1993): moves on high volume are
information-driven and persist; moves on low volume are liquidity-driven and
revert. If it holds, first-order return autocorrelation should be **more
negative after low-volume bars than after high-volume bars**.

That matters concretely. V16's frictionless profit factor is 1.057 against a
1.10 gate (`research/VIABILITY_VERDICT.md`), so the gross signal has to get
stronger, not cheaper. Concentrating reversion trades where reversion is
actually strongest is the only principled way to do that -- and it only works
if the effect is really there.

    python research/volume_information_test.py
    python research/volume_information_test.py --timeframe H1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.order_flow import (conditional_autocorrelation,  # noqa: E402
                                      relative_volume,
                                      volume_conditioned_profile)
from mt5_ai_bridge.persistence import log_returns  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--lookback", type=int, default=20)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    files = sorted(DATA_DIR.glob(f"*_{args.timeframe}.csv"))
    if not files:
        print(f"No {args.timeframe} data. Run tools/export_validation_history.py.")
        return 2

    print("=" * 88)
    print(f"DOES TICK VOLUME CARRY INFORMATION?  {args.timeframe}, "
          f"audited bars only")
    print("=" * 88)
    print("Campbell-Grossman-Wang predicts reversion concentrated in LOW "
          "volume,")
    print("so rho(low) should sit BELOW rho(high). Testing that directly.\n")

    head = (f"{'symbol':<9}{'pairs':>8}{'rho all':>10}{'rho low':>10}"
            f"{'rho high':>10}{'spread':>10}{'p(low)':>9}{'p(high)':>9}"
            f"{'CGW?':>7}")
    print(head)
    print("-" * len(head))

    results = {}
    for path in files:
        symbol = path.stem.rsplit("_", 1)[0]
        raw = pd.read_csv(path)
        if "tick_volume" not in raw.columns:
            print(f"{symbol:<9} no tick_volume column")
            continue
        audit = audit_bars(raw, symbol, args.timeframe)
        if not audit.usable:
            continue
        df = raw
        if audit.trusted_from:
            df = df[df["time"] >= audit.trusted_from].reset_index(drop=True)
        # Volume of zero means the bar was padded; drop those outright.
        df = df[df["tick_volume"] > 0].reset_index(drop=True)
        if len(df) < 2000:
            continue

        rets = log_returns(df["close"].tolist())
        rv = relative_volume(df["tick_volume"].tolist(), args.lookback)
        # log_returns drops one element from the front of the price series.
        rv = rv[1:1 + rets.size]

        try:
            res = conditional_autocorrelation(rets, rv)
        except ValueError as exc:
            print(f"{symbol:<9} {exc}")
            continue

        low, high, overall = res["low"], res["high"], res["overall"]
        mark = "yes" if res["supports_cgw"] else "no"
        print(f"{symbol:<9}{overall.n:>8}{overall.rho:>10.4f}"
              f"{low.rho:>10.4f}{high.rho:>10.4f}{res['rho_spread']:>10.4f}"
              f"{low.p_value:>9.4f}{high.p_value:>9.4f}{mark:>7}")

        results[symbol] = {
            "pairs": overall.n,
            "rho_all": round(overall.rho, 5),
            "rho_low": round(low.rho, 5),
            "rho_high": round(high.rho, 5),
            "rho_spread": res["rho_spread"],
            "p_low": round(low.p_value, 5),
            "p_high": round(high.p_value, 5),
            "supports_cgw": res["supports_cgw"],
            "low_verdict": low.verdict,
            "high_verdict": high.verdict,
        }

    if not results:
        print("\nNo symbol had usable volume data.")
        return 2

    supporting = [s for s, r in results.items() if r["supports_cgw"]]
    significant_low = [s for s, r in results.items() if r["p_low"] < 0.05]

    print("\n" + "=" * 88)
    print("VERDICT")
    print("=" * 88)
    print(f"  Symbols where low-volume reversion is significant : "
          f"{len(significant_low)}/{len(results)}"
          + (f"  ({', '.join(significant_low)})" if significant_low else ""))
    print(f"  Symbols supporting Campbell-Grossman-Wang         : "
          f"{len(supporting)}/{len(results)}"
          + (f"  ({', '.join(supporting)})" if supporting else ""))

    mean_spread = sum(r["rho_spread"] for r in results.values()) / len(results)
    print(f"  Mean rho(low) - rho(high) across symbols          : "
          f"{mean_spread:+.5f}")
    print("    negative = reversion concentrated in low volume, as predicted")

    if len(supporting) >= max(2, len(results) // 2):
        print("\n  The effect is present. Filtering reversion trades to "
              "low-volume stretches\n  is therefore a measurement-driven "
              "change, not a fitted one, and is worth\n  locking as a "
              "candidate.")
    else:
        print("\n  The effect is NOT reliably present. Building a "
              "volume-filtered strategy\n  would be fitting an effect the data "
              "does not support -- the same mistake\n  V15 made with trend "
              "persistence.")

    # --- is the effect graded, or one odd bucket? -------------------------
    print("\n" + "=" * 88)
    print("IS IT GRADED? autocorrelation by volume quintile")
    print("=" * 88)
    print("A real mechanism varies smoothly. One odd bucket is usually an "
          "outlier.\n")
    profiles = {}
    for path in files:
        symbol = path.stem.rsplit("_", 1)[0]
        if symbol not in results:
            continue
        raw = pd.read_csv(path)
        audit = audit_bars(raw, symbol, args.timeframe)
        df = raw
        if audit.trusted_from:
            df = df[df["time"] >= audit.trusted_from].reset_index(drop=True)
        df = df[df["tick_volume"] > 0].reset_index(drop=True)
        rets = log_returns(df["close"].tolist())
        try:
            profile = volume_conditioned_profile(
                rets, df["tick_volume"].tolist()[1:1 + rets.size],
                args.lookback)
        except ValueError:
            continue
        profiles[symbol] = profile
        cells = "  ".join(f"q{b['bucket']}:{b['rho']:+.4f}" for b in profile)
        print(f"  {symbol:<9}{cells}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"timeframe": args.timeframe, "lookback": args.lookback,
             "results": results, "quintile_profiles": profiles}, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
