"""Ten-year profitability and risk profile for the measured candidates.

Answers two concrete questions on the last decade of audited data:

* **What would it have made?** net, CAGR, per-year breakdown, losing years.
* **What is the risk ceiling?** both the limits the risk engine enforces and
  the drawdown actually realised, which are different numbers and both matter.

Costs are per instrument. Nothing here is optimised for the window -- the
parameters are the locked ones, and the ten-year slice is simply the last ten
years of the same audited series.

    python research/ten_year_profile.py
    python research/ten_year_profile.py --years 10 --cost typical
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5_ai_bridge.candidate_v16 import locked_config_v16, replay_v16  # noqa: E402
from mt5_ai_bridge.candidate_v19 import locked_config_v19, replay_v19  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import (Converter, cost_for,  # noqa: E402
                                       instrument_for, quote_currency_of)
from mt5_ai_bridge.risk_v18 import DrawdownGovernor, KillSwitch, RiskBudget  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"
START_BALANCE = 10_000.0


def equity_curve(trades, start=START_BALANCE):
    """Realised equity after each closed trade, in time order."""
    eq, bal = [start], start
    for t in sorted(trades, key=lambda x: x.exit_time):
        bal += t.profit
        eq.append(bal)
    return np.array(eq)


def drawdown_profile(eq: np.ndarray) -> dict:
    if eq.size < 2:
        return {"max_drawdown_pct": 0.0, "max_drawdown_money": 0.0,
                "longest_underwater_trades": 0}
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / np.where(peaks > 0, peaks, 1.0)
    # Longest stretch spent below a prior peak, measured in closed trades.
    underwater, longest, run = eq < peaks, 0, 0
    for flag in underwater:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return {
        "max_drawdown_pct": round(float(dd.max()) * 100.0, 2),
        "max_drawdown_money": round(float((peaks - eq).max()), 2),
        "longest_underwater_trades": int(longest),
    }


def yearly(trades) -> dict:
    by_year: dict = {}
    for t in trades:
        year = datetime.fromtimestamp(t.exit_time, tz=timezone.utc).year
        by_year.setdefault(year, {"trades": 0, "profit": 0.0})
        by_year[year]["trades"] += 1
        by_year[year]["profit"] = round(
            by_year[year]["profit"] + t.profit, 2)
    return dict(sorted(by_year.items()))


def load(symbol: str, timeframe: str, since: int):
    path = DATA_DIR / f"{symbol}_{timeframe}.csv"
    if not path.exists():
        return None
    raw = pd.read_csv(path)
    audit = audit_bars(raw, symbol, timeframe)
    if not audit.usable:
        return None
    df = load_csv(str(path)).reset_index(drop=True)
    if "tick_volume" in raw.columns:
        df = df.merge(raw[["time", "tick_volume"]], on="time", how="left")
    floor = max(audit.trusted_from or 0, since)
    df = df[df["time"] >= floor].reset_index(drop=True)
    if "tick_volume" in df.columns:
        df = df[df["tick_volume"].fillna(1) > 0].reset_index(drop=True)
    return df if len(df) >= 500 else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--cost", default="tight",
                   help="Most favourable realistic tier by default")
    p.add_argument("--symbols", nargs="+",
                   default=["EURUSD", "GBPUSD", "AUDUSD", "USDJPY",
                            "GBPJPY", "XAUUSD"])
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    now = datetime.now(timezone.utc)
    since = int((now.replace(year=now.year - args.years)).timestamp())
    since_label = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%d")

    v16, v19 = locked_config_v16(), locked_config_v19()

    converters = {}
    jpy_path = DATA_DIR / f"USDJPY_{args.timeframe}.csv"
    if jpy_path.exists():
        a = audit_bars(pd.read_csv(jpy_path), "USDJPY", args.timeframe)
        j = load_csv(str(jpy_path))
        if a.trusted_from:
            j = j[j["time"] >= a.trusted_from]
        if not j.empty:
            converters["JPY"] = Converter.from_frame(j, "USDJPY")

    print("=" * 94)
    print(f"{args.years}-YEAR PROFITABILITY AND RISK PROFILE")
    print("=" * 94)
    print(f"Window   : {since_label} to {now:%Y-%m-%d}   |   "
          f"{args.timeframe}   |   cost tier '{args.cost}' per instrument")
    print(f"Account  : ${START_BALANCE:,.0f} start, "
          f"{v16.risk_percent}% risk per trade, single position per symbol")
    print("Locked parameters; the window is simply the last "
          f"{args.years} years of audited data.\n")

    head = (f"{'model':<5}{'symbol':<9}{'trades':>8}{'net $':>11}{'CAGR':>8}"
            f"{'max DD':>9}{'DD $':>10}{'PF':>7}{'losing yrs':>12}")
    print(head)
    print("-" * len(head))

    rows = []
    for label, cfg, fn in (("V16", v16, replay_v16), ("V19", v19, replay_v19)):
        for symbol in args.symbols:
            bars = load(symbol, args.timeframe, since)
            if bars is None:
                continue
            if label == "V19" and "tick_volume" not in bars.columns:
                continue
            try:
                inst = instrument_for(
                    symbol, converters.get(quote_currency_of(symbol)))
            except ValueError:
                continue

            cost = cost_for(symbol, args.cost)
            res = fn(bars, cfg, cost, START_BALANCE, instrument=inst)
            if not res.trades:
                continue

            eq = equity_curve(res.trades)
            dd = drawdown_profile(eq)
            years = max(args.years, 1)
            final = eq[-1]
            cagr = ((final / START_BALANCE) ** (1 / years) - 1) * 100.0
            by_year = yearly(res.trades)
            losing = sum(1 for y in by_year.values() if y["profit"] < 0)

            rows.append({
                "model": label, "symbol": symbol,
                "trades": len(res.trades), "net": res.net_profit,
                "cagr_pct": round(cagr, 2),
                "max_drawdown_pct": dd["max_drawdown_pct"],
                "max_drawdown_money": dd["max_drawdown_money"],
                "longest_underwater_trades": dd["longest_underwater_trades"],
                "profit_factor": res.profit_factor,
                "losing_years": losing, "total_years": len(by_year),
                "by_year": by_year,
            })
            print(f"{label:<5}{symbol:<9}{len(res.trades):>8}"
                  f"{res.net_profit:>11.2f}{cagr:>7.2f}%"
                  f"{dd['max_drawdown_pct']:>8.2f}%{dd['max_drawdown_money']:>10.2f}"
                  f"{res.profit_factor:>7.3f}{losing:>7}/{len(by_year):<4}")

    if not rows:
        print("No usable data in the window.")
        return 2

    best = max(rows, key=lambda r: r["net"])
    print("\n" + "=" * 94)
    print("PROFITABILITY -- the best of these over the window")
    print("=" * 94)
    print(f"  {best['model']} on {best['symbol']}: "
          f"{best['net']:+,.2f} on ${START_BALANCE:,.0f} "
          f"over {args.years} years")
    print(f"  CAGR {best['cagr_pct']:+.2f}%   "
          f"profit factor {best['profit_factor']:.3f}   "
          f"{best['trades']} trades")
    print(f"  Losing years: {best['losing_years']} of {best['total_years']}")
    print("\n  Year by year:")
    for year, rec in best["by_year"].items():
        bar = "+" if rec["profit"] >= 0 else "-"
        print(f"    {year}  {rec['profit']:>10.2f}  "
              f"({rec['trades']:>4} trades) {bar * min(abs(int(rec['profit'] / 50)), 40)}")

    profitable = [r for r in rows if r["net"] > 0]
    print(f"\n  Configurations profitable over the window: "
          f"{len(profitable)}/{len(rows)}")

    # --- risk ceiling ------------------------------------------------------
    budget, gov, ks = RiskBudget(), DrawdownGovernor(), KillSwitch()
    print("\n" + "=" * 94)
    print("RISK CEILING -- what the system permits")
    print("=" * 94)
    print("  Enforced limits (mt5_ai_bridge/risk_v18.py):")
    print(f"    per trade                 {budget.max_symbol_risk_fraction:.1%} of equity")
    print(f"    aggregate open risk       {budget.max_total_risk_fraction:.1%}")
    print(f"    per currency              {budget.max_currency_risk_fraction:.1%}")
    print(f"    concurrent positions      {budget.max_concurrent_positions}")
    print(f"    daily loss kill switch    {ks.max_daily_loss_fraction:.1%}")
    print(f"    total drawdown kill       {ks.max_total_drawdown_fraction:.1%}  (latches)")
    print(f"    consecutive losses        {ks.max_consecutive_losses}")
    print(f"    exposure taper begins     {gov.soft_limit:.0%} drawdown")
    print(f"    exposure taper floor      {gov.floor:.0%} of normal size "
          f"at {gov.hard_limit:.0%}")

    worst = max(rows, key=lambda r: r["max_drawdown_pct"])
    print("\n  Realised in this window (single-symbol, before portfolio caps):")
    print(f"    deepest drawdown          {worst['max_drawdown_pct']:.2f}%  "
          f"({worst['model']} {worst['symbol']}, ${worst['max_drawdown_money']:,.2f})")
    print(f"    longest underwater run    "
          f"{worst['longest_underwater_trades']} consecutive trades")
    breached = [r for r in rows
                if r["max_drawdown_pct"] >= ks.max_total_drawdown_fraction * 100]
    print(f"    configs breaching the {ks.max_total_drawdown_fraction:.0%} "
          f"kill switch: {len(breached)}/{len(rows)}"
          + (f"  ({', '.join(r['model'] + ' ' + r['symbol'] for r in breached)})"
             if breached else ""))

    print("\n  Theoretical worst case before the system stops trading:")
    print(f"    {ks.max_total_drawdown_fraction:.0%} of equity "
          f"(${START_BALANCE * ks.max_total_drawdown_fraction:,.0f} on "
          f"${START_BALANCE:,.0f}), at which point the kill switch latches")
    print(f"    and does not clear on a recovery. Reaching it requires roughly")
    print(f"    {int(ks.max_total_drawdown_fraction / budget.max_symbol_risk_fraction)} "
          f"consecutive maximum-size full-stop losses, which the "
          f"{ks.max_consecutive_losses}-loss")
    print("    cutout and the exposure taper are both designed to prevent.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "years": args.years, "since": since_label,
            "timeframe": args.timeframe, "cost": args.cost,
            "start_balance": START_BALANCE,
            "risk_ceiling": {
                "per_trade": budget.max_symbol_risk_fraction,
                "aggregate": budget.max_total_risk_fraction,
                "per_currency": budget.max_currency_risk_fraction,
                "daily_loss_kill": ks.max_daily_loss_fraction,
                "total_drawdown_kill": ks.max_total_drawdown_fraction,
                "consecutive_losses": ks.max_consecutive_losses,
            },
            "rows": rows,
        }, indent=2, default=str))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
