"""Does timing the assets worth holding beat holding them?

Run on the symbols the walk-forward admission kept (IVV, VTI) plus the rest of
the tradable ETF set and gold, each judged against buy-and-hold of itself.

The bar is deliberately not "makes money". Holding IVV made 667% over this
window with no system at all. The rule earns its place only by improving
risk-adjusted return or by cutting drawdown enough to matter -- this account's
ceiling is 10% and buy-and-hold breaches it several times over.

    python research/tactical_test.py --json-out research/tactical.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5_ai_bridge.corporate_actions import adjust_for_splits  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.instruments import cost_for, instrument_for  # noqa: E402
from mt5_ai_bridge.tactical_allocation import (locked_tactical_config,  # noqa: E402
                                               replay_timing)

DATA = Path(__file__).resolve().parents[1] / "research" / "data"
ADMITTED = ("IVV", "VTI")
ALSO = ("ONEQ", "IWM", "TQQQ", "EEM", "XAUUSD")


def spread_percent(symbol: str) -> float:
    """Typical round-trip spread as a percent of price, from the instrument."""
    try:
        instrument = instrument_for(symbol)
        cost = cost_for(symbol, "typical")
    except (KeyError, ValueError):
        return 0.05
    pips = getattr(cost, "spread_pips", None)
    if pips is None:
        pips = instrument.spread_tiers[1]
    return float(pips) * instrument.pip


def load(symbol: str):
    path = DATA / f"{symbol}_D1.csv"
    if not path.exists():
        return None
    frame, _ = adjust_for_splits(load_csv(str(path)).reset_index(drop=True))
    return frame.reset_index(drop=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", nargs="+",
                        default=list(ADMITTED) + list(ALSO))
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    cfg = locked_tactical_config()
    print("=" * 96)
    print(f"TACTICAL {cfg.sma_months}-MONTH MA TIMING vs BUY-AND-HOLD "
          "-- one locked trial")
    print("=" * 96)
    header = (f"{'symbol':<8}{'periods':>8}{'in mkt':>8}{'switch':>7}"
              f"{'strat CAGR':>11}{'hold CAGR':>11}{'strat DD':>10}"
              f"{'hold DD':>9}{'strat SR':>10}{'hold SR':>9}")
    print(header)
    print("-" * len(header))

    payload, rows = {}, []
    for symbol in args.symbols:
        bars = load(symbol)
        if bars is None or len(bars) < cfg.sma_days + 40:
            continue
        # Price is a percent of itself, so a percent spread converts directly.
        price = float(bars["close"].median())
        spread = spread_percent(symbol) / price * 100.0 if price else 0.0
        result = replay_timing(bars, cfg, spread, symbol)
        s = result.summary()
        rows.append(s)
        payload[symbol] = s
        print(f"{symbol:<8}{s['periods']:>8}{s['time_in_market']*100:>7.0f}%"
              f"{s['switches']:>7}"
              f"{s['strategy']['annual_pct']:>10.2f}%"
              f"{s['buy_and_hold']['annual_pct']:>10.2f}%"
              f"{s['strategy']['max_drawdown_pct']:>9.1f}%"
              f"{s['buy_and_hold']['max_drawdown_pct']:>8.1f}%"
              f"{s['strategy']['sharpe']:>10.3f}{s['buy_and_hold']['sharpe']:>9.3f}")

    if not rows:
        print("No usable data.")
        return 2

    print("\n" + "=" * 96)
    print("READING")
    print("=" * 96)
    better_sharpe = [r for r in rows if r["beats_hold_on_sharpe"]]
    lower_dd = [r for r in rows if r["drawdown_reduction_pct"] > 0]
    under_ten = [r for r in rows if r["strategy"]["max_drawdown_pct"] <= 10.0]
    print(f"  Beats buy-and-hold on Sharpe : {len(better_sharpe)}/{len(rows)}")
    print(f"  Reduces max drawdown         : {len(lower_dd)}/{len(rows)}")
    print(f"  Drawdown within the 10% ceiling: {len(under_ten)}/{len(rows)}")
    mean_dd = float(np.mean([r["drawdown_reduction_pct"] for r in rows]))
    mean_give = float(np.mean([r["buy_and_hold"]["annual_pct"]
                               - r["strategy"]["annual_pct"] for r in rows]))
    print(f"  Mean drawdown reduction      : {mean_dd:+.1f} points")
    print(f"  Mean annual return given up  : {mean_give:+.2f} points")

    payload["summary"] = {
        "beats_hold_on_sharpe": len(better_sharpe), "symbols": len(rows),
        "reduces_drawdown": len(lower_dd),
        "within_10pct_ceiling": len(under_ten),
        "mean_drawdown_reduction_pts": round(mean_dd, 2),
        "mean_annual_return_given_up_pts": round(mean_give, 2),
    }

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2),
                                       encoding="utf-8")
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
