"""Which of these markets actually trend? Measured before any strategy runs.

If a symbol's returns are a random walk at the strategy's holding horizon, no
parameterisation of a momentum rule will produce an edge on it, and tuning one
until it backtests well is fitting noise. This scan answers that question
independently of P&L.

Run (after tools/export_validation_history.py):
    python research/v15_persistence_scan.py
    python research/v15_persistence_scan.py --timeframe D1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.persistence import (hurst_exponent, log_returns,  # noqa: E402
                                       variance_ratio_profile)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"

# H4 horizons: 6 bars = 1 day, 30 = 1 week, 120 = 1 month.
HORIZONS = (2, 6, 30, 120)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--since", type=int, default=None,
                   help="Ignore bars before this epoch second (e.g. drop "
                        "synthetic pre-1999 EURUSD)")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    files = sorted(DATA_DIR.glob(f"*_{args.timeframe}.csv"))
    if not files:
        print(f"No {args.timeframe} data in {DATA_DIR}. Run "
              "tools/export_validation_history.py first.")
        return 2

    print(f"Variance-ratio test on {args.timeframe} log returns")
    print("VR > 1 trends, VR = 1 random walk, VR < 1 mean-reverts. "
          "Bold z beyond +/-1.96 is significant at 5%.\n")

    cols = "  ".join(f"q={q:<3}" for q in HORIZONS)
    print(f"{'symbol':<9}{'bars':>7}  {cols}   {'hurst':>6}  verdict(q=6)")
    print("-" * (9 + 7 + 2 + len(cols) + 3 + 6 + 16))

    out = {}
    for path in files:
        symbol = path.stem.rsplit("_", 1)[0]
        df = load_csv(str(path))
        if args.since:
            df = df[df["time"] >= args.since]
        rets = log_returns(df["close"].tolist())
        if rets.size < max(HORIZONS) * 2:
            print(f"{symbol:<9}{len(df):>7}  too few bars")
            continue

        profile = variance_ratio_profile(rets, HORIZONS)
        by_q = {vr.q: vr for vr in profile}
        try:
            hurst = hurst_exponent(rets)
        except ValueError:
            hurst = float("nan")

        cells = []
        for q in HORIZONS:
            vr = by_q.get(q)
            if vr is None:
                cells.append("  --  ")
                continue
            star = "*" if vr.p_value < 0.05 else " "
            cells.append(f"{vr.ratio:5.3f}{star}")
        verdict = by_q[6].verdict if 6 in by_q else "?"
        print(f"{symbol:<9}{len(df):>7}  " + "  ".join(cells)
              + f"   {hurst:6.3f}  {verdict}")

        out[symbol] = {
            "bars": len(df),
            "hurst": None if hurst != hurst else round(hurst, 4),
            "variance_ratios": {
                str(vr.q): {"ratio": round(vr.ratio, 4),
                            "z": round(vr.z_score, 3),
                            "p": round(vr.p_value, 4),
                            "verdict": vr.verdict}
                for vr in profile},
        }

    print("\n* = significant at the 5% level")
    print("\nReading: a symbol whose VR is at or below 1.0 across the holding")
    print("horizon does not trend at that horizon. A momentum rule cannot be")
    print("tuned into an edge there -- any profitable backtest on it is fitted.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
