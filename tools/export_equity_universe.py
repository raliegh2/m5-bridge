"""Export a liquid, tradable US equity universe from the MT5 terminal.

The account can order 11,438 individual equities and nothing here has ever been
run against them. This builds the dataset for that: daily bars plus the
contract facts a small account needs (price, spread, minimum lot).

Selection is by rules, in this order, and every threshold is recorded in the
manifest so the universe can be rebuilt or challenged:

1. fully tradable (``trade_mode == FULL``);
2. at least ``--min-years`` of daily history;
3. median *historical* spread under ``--max-spread-pct`` of price -- a 0.5%
   round trip eats any cross-sectional signal before it starts;
4. price inside ``--min-price``/``--max-price``, because one share is the
   minimum order and a $900 share is an untradable fraction of a $4,802
   account;
5. the ``--top`` most liquid survivors by median dollar volume.

Screening uses the spread recorded on each daily bar, not ``symbol_info.ask``.
Only 25 of the 11,438 tradable equities carry a live quote at any moment --
the rest read ``ask = 0`` until they are selected into Market Watch, so a
screen on the live quote silently discards 99.8% of the universe. The bars
also give the spread *at the time*, which is what a backtest should pay.

**Survivorship.** The terminal lists what is listed *today*. Companies that
failed between 2003 and now are absent, so any backtest on this universe is an
upper bound, not an estimate. That is a property of the data source and cannot
be fixed from here -- it can only be stated, and it is, in the manifest.

    python tools/export_equity_universe.py --top 300
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research" / "data" / "equities"
MANIFEST = ROOT / "research" / "equity_universe.json"
COLUMNS = ("time", "open", "high", "low", "close", "tick_volume")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=300)
    parser.add_argument("--min-years", type=float, default=15.0)
    parser.add_argument("--max-spread-pct", type=float, default=0.30)
    parser.add_argument("--min-price", type=float, default=5.0)
    parser.add_argument("--max-price", type=float, default=400.0)
    parser.add_argument("--bars", type=int, default=100_000)
    parser.add_argument("--group", default="Nasdaq\\Stock",
                        help="symbol path prefix; the default excludes the "
                             "5,273 ETFs and 294 warrants, because this is an "
                             "equity cross-section")
    parser.add_argument("--max-scan", type=int, default=None,
                        help="scan only the first N symbols alphabetically. "
                             "The terminal downloads a symbol's whole history "
                             "on first touch, ~1.2s each, so a full 6,229-name "
                             "sweep takes about two hours. Alphabetical order "
                             "is unrelated to returns, so a bounded scan "
                             "biases the universe's size, not its signal.")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    import MetaTrader5 as mt5

    if not mt5.initialize():
        print("MT5 initialize failed:", mt5.last_error())
        return 1

    account = mt5.account_info()
    print(f"account {account.login} on {account.server}, "
          f"balance {account.balance:.2f} {account.currency}")

    full = 4
    candidates = [info for info in mt5.symbols_get()
                  if info.trade_mode == full
                  and info.path.startswith(args.group)]
    candidates.sort(key=lambda info: info.name)
    if args.max_scan:
        candidates = candidates[:args.max_scan]
    print(f"{len(candidates)} fully tradable symbols under {args.group!r} "
          f"to scan", flush=True)

    min_bars = int(args.min_years * 252)
    rows = []
    dropped = {"no_history": 0, "short_history": 0, "price": 0, "spread": 0}
    for i, info in enumerate(candidates, 1):
        if i % 500 == 0:
            print(f"  scanned {i}/{len(candidates)}, "
                  f"kept {len(rows)}", flush=True)
        if not mt5.symbol_select(info.name, True):
            dropped["no_history"] += 1
            continue
        rates = mt5.copy_rates_from_pos(info.name, mt5.TIMEFRAME_D1, 0,
                                        args.bars)
        if rates is None or len(rates) == 0:
            dropped["no_history"] += 1
            continue
        if len(rates) < min_bars:
            dropped["short_history"] += 1
            continue
        frame = pd.DataFrame(rates)
        price = float(frame["close"].tail(250).median())
        if not args.min_price <= price <= args.max_price:
            dropped["price"] += 1
            continue
        # Spread as recorded on the bars themselves, in points.
        spread_points = float(frame["spread"].tail(500).median()) \
            if "spread" in frame.columns else 0.0
        spread_pct = spread_points * info.point / price * 100.0 if price else 999
        if spread_pct > args.max_spread_pct:
            dropped["spread"] += 1
            continue
        dollar_volume = float(
            (frame["close"] * frame["tick_volume"]).tail(500).median())
        rows.append({"symbol": info.name, "bars": len(frame),
                     "price": round(price, 2),
                     "spread_pct": round(spread_pct, 4),
                     "min_lot": float(info.volume_min),
                     "dollar_volume": dollar_volume, "frame": frame})
    print(f"{len(rows)} symbols pass every screen; dropped {dropped}")

    rows.sort(key=lambda r: r["dollar_volume"], reverse=True)
    kept = rows[:args.top]

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for row in kept:
        frame = row.pop("frame")
        frame = frame[list(COLUMNS)]
        frame.to_csv(args.out / f"{row['symbol']}_D1.csv", index=False)
        span = pd.to_datetime(frame["time"], unit="s", utc=True)
        row["start"] = int(frame["time"].iloc[0])
        row["end"] = int(frame["time"].iloc[-1])
        row["years"] = round((row["end"] - row["start"]) / (365.25 * 86_400), 1)
        row["dollar_volume"] = round(row["dollar_volume"], 0)
        manifest.append(row)
        print(f"  {row['symbol']:<6} {len(frame):>5} bars "
              f"{span.iloc[0]:%Y-%m-%d}..{span.iloc[-1]:%Y-%m-%d} "
              f"spread {row['spread_pct']:.3f}%")

    MANIFEST.write_text(json.dumps({
        "selection": {
            "top": args.top, "min_years": args.min_years,
            "max_spread_pct": args.max_spread_pct,
            "min_price": args.min_price, "max_price": args.max_price,
            "group": args.group, "max_scan": args.max_scan,
        },
        "survivorship_warning": (
            "The terminal lists only symbols listed today. Companies delisted "
            "between 2003 and now are absent, so results on this universe are "
            "an upper bound rather than an estimate."),
        "count": len(manifest),
        "symbols": manifest,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {len(manifest)} symbols to {args.out}")
    print(f"Manifest: {MANIFEST}")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
