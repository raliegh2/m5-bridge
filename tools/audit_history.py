"""Audit every exported price series and say which data is fit to use.

Prints a verdict per symbol/timeframe and, where a series only becomes
trustworthy partway through, the date from which it can be used.

    python tools/audit_history.py
    python tools/audit_history.py --timeframe H4 --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "research" / "data"

BAR_SECONDS = {"D1": 86_400, "H4": 14_400, "H1": 3_600, "M30": 1_800,
               "M15": 900, "M5": 300, "M1": 60}


def _date(ts) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def main(argv=None) -> int:
    import pandas as pd

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=str(DATA_DIR))
    p.add_argument("--timeframe", default=None,
                   help="Audit only this timeframe (default: all found)")
    p.add_argument("--verbose", action="store_true",
                   help="List every issue, not just fatal and major ones")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    data_dir = Path(args.dir)
    pattern = f"*_{args.timeframe}.csv" if args.timeframe else "*.csv"
    files = sorted(f for f in data_dir.glob(pattern) if f.stem != "manifest")
    if not files:
        print(f"No CSVs in {data_dir}. Run tools/export_validation_history.py.")
        return 2

    print(f"Auditing {len(files)} series in {data_dir}\n")
    header = (f"{'symbol':<9}{'tf':<5}{'bars':>8}  {'range':<24}"
              f"{'verdict':<18}{'trusted from':<14}")
    print(header)
    print("-" * len(header))

    results = []
    for path in files:
        stem = path.stem
        if "_" not in stem:
            continue
        symbol, tf = stem.rsplit("_", 1)
        df = pd.read_csv(path)
        res = audit_bars(df, symbol, tf, BAR_SECONDS.get(tf))
        results.append(res)
        rng = f"{_date(res.start)} -> {_date(res.end)} "
        trusted = (res.trusted_from_date
                   if res.trusted_from and res.trusted_from > (res.start or 0)
                   else "(all)")
        print(f"{symbol:<9}{tf:<5}{res.bars:>8}  {rng:<24}"
              f"{res.verdict:<18}{trusted:<14}")

    # --- detail ---------------------------------------------------------
    shown = False
    for res in results:
        issues = [i for i in res.issues
                  if args.verbose or i.severity in ("fatal", "major")]
        if not issues:
            continue
        if not shown:
            print("\nIssues:")
            shown = True
        print(f"\n  {res.symbol} {res.timeframe}")
        for issue in issues:
            print(f"    {issue}")
    if not shown:
        print("\nNo fatal or major issues found.")

    # --- what to actually use -------------------------------------------
    usable = [r for r in results if r.usable]
    clean = [r for r in usable if r.verdict == "USABLE"]
    partial = [r for r in usable if r.verdict == "USABLE FROM"]
    careful = [r for r in usable if r.verdict == "USABLE WITH CARE"]
    dead = [r for r in results if not r.usable]

    print("\n" + "=" * 66)
    print("WHAT IS FIT TO BACKTEST ON")
    print("=" * 66)
    print(f"  clean               : {len(clean)}")
    print(f"  usable from a date  : {len(partial)}")
    print(f"  usable with care    : {len(careful)}")
    print(f"  unusable            : {len(dead)}")
    if partial:
        print("\n  Series needing a start date:")
        for r in partial:
            print(f"    {r.symbol} {r.timeframe}: use --since "
                  f"{r.trusted_from}  ({r.trusted_from_date} onward)")
    if dead:
        print("\n  Unusable:")
        for r in dead:
            reasons = ", ".join(i.code for i in r.fatal) or "empty"
            print(f"    {r.symbol} {r.timeframe}: {reasons}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            [r.summary() for r in results], indent=2))
        print(f"\nWrote {args.json_out}")

    return 0 if not dead else 1


if __name__ == "__main__":
    sys.exit(main())
