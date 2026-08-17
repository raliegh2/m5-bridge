"""The repository's own ICT engine, measured with costs and partial exits.

Every prior ICT report scored trades as pure R multiples with no spread,
commission or slippage -- ``research/v14_4_cost_stress_report.py`` says exactly
that, and warns the edge disappears within about a pip. This is the first run
that charges the costs, on audited history, out of sample.

Three things are measured together, because they interact:

* **costs**, per instrument, at the tight tier;
* **partial profit taking**, half off at 1R with the stop to breakeven, which
  should cut drawdown and may cut expectancy with it;
* **out-of-sample discipline** -- the profile is chosen on the training slice
  and scored on the slice after it, so a favourable profile cannot be picked
  with hindsight.

Note that a partial fill crosses the spread again, so cost is charged per leg.
On the tight ICT stops that is not a rounding error, and it is the honest
reason partials are not free.

    python research/ict_cost_and_partials.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.data_audit import audit_bars  # noqa: E402
from mt5_ai_bridge.instruments import (Converter, cost_for,  # noqa: E402
                                       instrument_for, quote_currency_of)
from mt5_ai_bridge.partial_exits import (HALF_AT_1R, PartialPlan,  # noqa: E402
                                         simulate_with_partials,
                                         summarise_outcomes)
from mt5_ai_bridge.v14_3_all_symbol_ict import (PROFILES,  # noqa: E402
                                                generate_candidates,
                                                prepare_frames)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"
SYMBOLS = ("EURUSD", "AUDUSD", "USDJPY")


def load_tf(symbol: str, timeframe: str, minimum: int = 500):
    path = DATA_DIR / f"{symbol}_{timeframe}.csv"
    if not path.exists():
        return None
    audit = audit_bars(pd.read_csv(path), symbol, timeframe)
    if not audit.usable:
        return None
    df = load_csv(str(path)).reset_index(drop=True)
    if audit.trusted_from:
        df = df[df["time"] >= audit.trusted_from].reset_index(drop=True)
    if len(df) < minimum:
        return None
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def load_h1(symbol: str):
    return load_tf(symbol, "H1", minimum=20_000)


def score(frame, candidates, inst, cost, plan, risk_fraction):
    """Replay candidates through the partial-exit simulator with costs."""
    outcomes = []
    round_trip_price = cost.round_trip_pips * inst.pip
    for _, c in candidates.iterrows():
        risk_price = float(c["risk_price"])
        if not np.isfinite(risk_price) or risk_price <= 0:
            continue
        cost_r = round_trip_price / risk_price
        out = simulate_with_partials(
            frame, int(c["signal_index"]), 1 if c["side"] == "BUY" else -1,
            float(c["entry_price"]), float(c["stop_price"]),
            float(c["target_r"]), int(c["max_holding_hours"]),
            plan=plan, cost_r=cost_r)
        outcomes.append(out)
    return summarise_outcomes(outcomes, risk_fraction=risk_fraction)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cost", default="tight")
    p.add_argument("--risk", type=float, default=0.005)
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    converters = {}
    jpy = load_h1("USDJPY")
    if jpy is not None:
        j = jpy.copy()
        j["time"] = j["time"].astype("int64") // 10 ** 9
        converters["JPY"] = Converter.from_frame(j, "USDJPY")

    print("=" * 96)
    print("ICT ENGINE, WITH COSTS AND PARTIAL EXITS -- out of sample")
    print("=" * 96)
    print(f"Cost tier '{args.cost}' per instrument, charged per filled leg. "
          f"Risk {args.risk:.2%}/trade.")
    print("Profile selected on the first 60% of history, scored on the last "
          "40%.\n")

    head = (f"{'symbol':<8}{'profile':<18}{'plan':<12}{'trades':>8}"
            f"{'exp R':>9}{'PF':>8}{'win%':>7}{'return':>10}{'max DD':>9}")
    print(head)
    print("-" * len(head))

    rows = []
    for symbol in SYMBOLS:
        h1 = load_h1(symbol)
        if h1 is None:
            print(f"{symbol:<8} no usable H1 data")
            continue
        try:
            inst = instrument_for(symbol,
                                  converters.get(quote_currency_of(symbol)))
        except ValueError as exc:
            print(f"{symbol:<8} {str(exc).split(';')[0]}")
            continue
        h4 = load_tf(symbol, "H4")
        d1 = load_tf(symbol, "D1")
        if h4 is None or d1 is None:
            print(f"{symbol:<8} missing H4/D1 for the bias filters")
            continue
        cost = cost_for(symbol, args.cost)

        # The ICT engine needs H4 and D1 for its bias filters; prepare_frames
        # merges them onto H1 as-of, so no future bar leaks in.
        frame, _, _ = prepare_frames(h1, h4, d1)
        split_at = int(len(frame) * 0.6)
        split_time = frame.iloc[split_at]["time"]

        # --- choose a profile on the training slice only -------------------
        best_profile, best_expectancy = None, -np.inf
        for profile in PROFILES[symbol]:
            cands = generate_candidates(symbol, frame, profile)
            if cands.empty:
                continue
            train = cands[cands["entry_time"] < split_time]
            if len(train) < 50:
                continue
            stats = score(frame, train, inst, cost, PartialPlan(), args.risk)
            if stats["expectancy_r"] > best_expectancy:
                best_profile, best_expectancy = profile, stats["expectancy_r"]

        if best_profile is None:
            print(f"{symbol:<8} no profile produced enough training trades")
            continue

        cands = generate_candidates(symbol, frame, best_profile)
        test = cands[cands["entry_time"] >= split_time]
        if len(test) < 30:
            print(f"{symbol:<8}{best_profile.name:<18} too few OOS trades "
                  f"({len(test)})")
            continue

        for label, plan in (("flat", PartialPlan()),
                            ("half@1R+BE", HALF_AT_1R)):
            stats = score(frame, test, inst, cost, plan, args.risk)
            rows.append({"symbol": symbol, "profile": best_profile.name,
                         "plan": label, **stats})
            print(f"{symbol:<8}{best_profile.name:<18}{label:<12}"
                  f"{stats['trades']:>8}{stats['expectancy_r']:>9.4f}"
                  f"{stats['profit_factor']:>8.3f}"
                  f"{stats['win_rate'] * 100:>6.1f}%"
                  f"{stats['return_pct']:>9.2f}%{stats['max_drawdown_pct']:>8.2f}%")

    if not rows:
        print("\nNothing could be scored.")
        return 2

    # --- did partials help? ------------------------------------------------
    print("\n" + "=" * 96)
    print("DID PARTIAL PROFITS HELP?")
    print("=" * 96)
    print(f"{'symbol':<8}{'expectancy':>22}{'drawdown':>20}"
          f"{'return/DD':>22}")
    print(f"{'':<8}{'flat -> partial':>22}{'flat -> partial':>20}"
          f"{'flat -> partial':>22}")
    print("-" * 72)
    improved_rr = 0
    pairs = 0
    for symbol in {r["symbol"] for r in rows}:
        flat = next((r for r in rows if r["symbol"] == symbol
                     and r["plan"] == "flat"), None)
        part = next((r for r in rows if r["symbol"] == symbol
                     and r["plan"] == "half@1R+BE"), None)
        if not flat or not part:
            continue
        pairs += 1
        rr_flat = (flat["return_pct"] / flat["max_drawdown_pct"]
                   if flat["max_drawdown_pct"] > 0 else 0.0)
        rr_part = (part["return_pct"] / part["max_drawdown_pct"]
                   if part["max_drawdown_pct"] > 0 else 0.0)
        if rr_part > rr_flat:
            improved_rr += 1
        print(f"{symbol:<8}{flat['expectancy_r']:>10.4f} ->"
              f"{part['expectancy_r']:>9.4f}"
              f"{flat['max_drawdown_pct']:>10.2f}% ->"
              f"{part['max_drawdown_pct']:>7.2f}%"
              f"{rr_flat:>11.3f} ->{rr_part:>9.3f}")

    print(f"\n  Partials improved return/drawdown on {improved_rr}/{pairs} "
          "symbols.")
    print("  Expectancy is expected to FALL -- scaling out caps the winners "
          "that pay for\n  the losses. It earns its place only if drawdown "
          "falls by more.")

    under_10 = [r for r in rows if r["max_drawdown_pct"] < 10.0]
    profitable = [r for r in rows if r["return_pct"] > 0]
    print(f"\n  Configurations under a 10% drawdown : {len(under_10)}/{len(rows)}")
    print(f"  Configurations profitable           : {len(profitable)}/{len(rows)}")
    both = [r for r in rows if r["max_drawdown_pct"] < 10.0
            and r["return_pct"] > 0]
    print(f"  Both profitable AND under 10% DD    : {len(both)}/{len(rows)}"
          + (f"  ({', '.join(r['symbol'] + '/' + r['plan'] for r in both)})"
             if both else ""))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2, default=str))
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
