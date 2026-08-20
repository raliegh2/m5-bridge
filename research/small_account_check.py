"""Can a real, small account actually place these trades?

Every backtest in this repository assumed $10,000. The live account holds
$4,802. That difference is not cosmetic: position sizing is bounded below by
the broker's minimum lot, and on a small account the minimum trade can be
larger than the intended risk budget. When that happens the strategy is not
merely less profitable -- it is untradeable at the intended risk, and the
account is forced to take more risk per trade than the sizing model believes.

For each instrument this reports:

* the **minimum position** the broker will accept;
* the **exposure** that represents, as a share of the account;
* the **risk** it carries at a realistic ATR-scaled stop, against the intended
  budget;
* whether the account can size **granularly** -- if the minimum is already over
  budget, every position is oversized and the risk engine's arithmetic is a
  fiction.

    python research/small_account_check.py --balance 4802
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v16 import locked_config_v16  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import instrument_for  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"

# Broker minimum volume, read from the terminal earlier.
MIN_LOT = {
    "EURUSD": 0.01, "GBPUSD": 0.01, "AUDUSD": 0.01, "USDJPY": 0.01,
    "GBPJPY": 0.01, "XAUUSD": 0.01,
    "US30": 0.10, "US500": 0.10, "USTEC": 0.10, "US2000": 1.00,
    "ONEQ": 1.00, "IVV": 1.00, "IWM": 1.00, "VTI": 1.00,
    "TQQQ": 1.00, "EEM": 1.00,
}
TRADABLE = {
    "EURUSD": True, "GBPUSD": True, "AUDUSD": True, "USDJPY": True,
    "GBPJPY": True, "XAUUSD": True,
    "US30": False, "US500": False, "USTEC": False, "US2000": False,
    "ONEQ": True, "IVV": True, "IWM": True, "VTI": True,
    "TQQQ": True, "EEM": True,
}


def load_any(symbol: str):
    for tf in ("D1", "H4"):
        path = DATA_DIR / f"{symbol}_{tf}.csv"
        if not path.exists():
            continue
        audit = audit_bars(pd.read_csv(path), symbol, tf)
        if not audit.usable:
            continue
        df = load_csv(str(path)).reset_index(drop=True)
        if audit.trusted_from:
            df = df[df["time"] >= audit.trusted_from].reset_index(drop=True)
        if len(df) > 300:
            return df, tf
    return None, None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--balance", type=float, default=4802.43)
    p.add_argument("--risk-pct", type=float, default=0.5,
                   help="Intended risk per trade, %% of balance")
    p.add_argument("--leverage", type=float, default=100.0,
                   help="Account leverage, for the margin figure")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cfg = locked_config_v16()
    budget = args.balance * args.risk_pct / 100.0

    print("=" * 100)
    print(f"CAN A ${args.balance:,.2f} ACCOUNT TRADE THIS?")
    print("=" * 100)
    print(f"Intended risk per trade: {args.risk_pct}% = ${budget:,.2f}")
    print("Stop distance is the locked V16 stop (2 sigma of a 20-bar window), "
          "measured per symbol.\n")

    head = (f"{'symbol':<8}{'trade?':>7}{'price':>10}{'min lot':>9}"
            f"{'exposure':>11}{'% acct':>8}{'stop':>9}{'min risk':>10}"
            f"{'% acct':>8}{'sizable?':>10}")
    print(head)
    print("-" * len(head))

    rows = []
    for symbol in MIN_LOT:
        bars, tf = load_any(symbol)
        if bars is None:
            continue
        try:
            inst = instrument_for(symbol)
        except ValueError:
            continue

        price = float(bars["close"].iloc[-1])
        # Typical stop: (stop_z - entry_z) sigma of the 20-bar close window,
        # matching what the locked V16 config actually uses.
        sd = float(bars["close"].rolling(cfg.lookback).std(ddof=0)
                   .dropna().median())
        stop_distance = (cfg.stop_z - cfg.entry_z) * sd
        if not np.isfinite(stop_distance) or stop_distance <= 0:
            continue

        min_lot = MIN_LOT[symbol]
        exposure = price * inst.contract_size * min_lot
        stop_pips = stop_distance / inst.pip
        min_risk = stop_pips * inst.pip_value_per_lot * min_lot

        exposure_pct = exposure / args.balance * 100.0
        risk_pct = min_risk / args.balance * 100.0
        sizable = min_risk <= budget
        rows.append({
            "symbol": symbol, "tradable": TRADABLE.get(symbol, True),
            "timeframe": tf, "price": round(price, 2), "min_lot": min_lot,
            "exposure": round(exposure, 2),
            "exposure_pct": round(exposure_pct, 1),
            "stop_distance": round(stop_distance, 4),
            "min_risk": round(min_risk, 2),
            "min_risk_pct": round(risk_pct, 3),
            "sizable": bool(sizable),
        })
        mark = "yes" if sizable else "TOO BIG"
        trade = "yes" if TRADABLE.get(symbol, True) else "NO"
        print(f"{symbol:<8}{trade:>7}{price:>10.2f}{min_lot:>9.2f}"
              f"{exposure:>11,.0f}{exposure_pct:>7.0f}%{stop_distance:>9.2f}"
              f"{min_risk:>10.2f}{risk_pct:>7.2f}%{mark:>10}")

    if not rows:
        print("Nothing to assess.")
        return 2

    tradable = [r for r in rows if r["tradable"]]
    oversized = [r for r in tradable if not r["sizable"]]
    ok = [r for r in tradable if r["sizable"]]

    print("\n" + "=" * 100)
    print("READING")
    print("=" * 100)
    print(f"  Tradable on this account          : {len(tradable)}/{len(rows)}")
    print(f"  Of those, sizable within budget   : {len(ok)}")
    print(f"  Of those, minimum exceeds budget  : {len(oversized)}")
    if oversized:
        print("\n  Minimum position is larger than the intended risk on:")
        for r in oversized:
            over = r["min_risk"] / budget
            print(f"    {r['symbol']:<8} min risk ${r['min_risk']:,.2f} = "
                  f"{r['min_risk_pct']:.2f}% of account "
                  f"({over:.1f}x the {args.risk_pct}% budget)")
        print("\n  On these the risk engine's sizing is a fiction: every "
              "position is forced\n  above budget, so the drawdown ceiling it "
              "computes will not hold.")

    heavy = [r for r in tradable if r["exposure_pct"] > 50]
    if heavy:
        print(f"\n  Minimum position exceeds 50% of the account in notional "
              f"on {len(heavy)}:")
        for r in heavy:
            margin = r["exposure"] / args.leverage
            print(f"    {r['symbol']:<8} ${r['exposure']:,.0f} notional = "
                  f"{r['exposure_pct']:.0f}% of ${args.balance:,.0f}, but only "
                  f"${margin:,.2f} margin at 1:{args.leverage:g}")
        print("  Notional as a share of equity is the WRONG lens here: a"
              " routine 0.4-lot")
        print("  EURUSD position is ~480% of a $10k account in notional and"
              " entirely normal.")
        print("  The binding constraint is the min-lot risk above, not"
              " exposure.")

    print(f"\n  Practical conclusion for a ${args.balance:,.0f} account:")
    if ok:
        print(f"    Sizable at {args.risk_pct}%: "
              f"{', '.join(r['symbol'] for r in ok)}")
    if oversized:
        print(f"    Not sizable: {', '.join(r['symbol'] for r in oversized)}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"balance": args.balance, "risk_pct": args.risk_pct,
             "risk_budget": round(budget, 2), "rows": rows}, indent=2))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
