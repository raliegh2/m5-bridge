"""Diff every hard-coded instrument spec against what the broker actually says.

Four bugs in this repository shared one cause: a contract convention assumed in
code and never checked against reality.

* gold priced with a 100,000-unit FX contract instead of 100 ounces, which
  overstated its P&L until the min-lot clamp made it look like the only
  profitable instrument;
* JPY pairs priced as if their P&L were already in dollars;
* index CFDs charged a $7/lot FX ECN commission, which made the "tight" cost
  tier more expensive than "wide";
* minimum lot sizes assumed to be 0.01 everywhere, when indices are 0.10-1.00
  and ETFs are 1 share -- the reason a drawdown ceiling computed on a small
  account does not hold.

None of these raised an error. Each produced a plausible number that was wrong.
This tool makes them loud: it reads ``symbol_info`` for every instrument in
``instruments.py`` and reports any field that disagrees.

Run it after adding an instrument, changing brokers, or whenever a result looks
too good.

    python tools/verify_instruments.py
    python tools/verify_instruments.py --strict     # exit 1 on any mismatch
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_ai_bridge.instruments import (CONVERTIBLE, INSTRUMENTS,  # noqa: E402
                                       quote_currency_of)

# A pip is a chosen unit, not an MT5 field, and it is P&L-neutral: `settle`
# computes gross from contract_size, and pip only scales the spread figure and
# the stop-in-pips used for sizing, which cancel. So 1 index point and 1 cent
# are both valid units for US30 provided the spread tiers use the same one.
#
# What is NOT valid is a pip the broker cannot express -- one that is not a
# whole multiple of its minimum tick. That would mean quoting a spread the
# market cannot actually trade at.
def _pip_is_expressible(pip: float, point: float) -> bool:
    if point <= 0 or pip <= 0:
        return False
    ratio = pip / point
    return abs(ratio - round(ratio)) <= 1e-6 and round(ratio) >= 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero if anything disagrees")
    args = p.parse_args(argv)

    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("MetaTrader5 not installed; cannot verify against a broker.")
        return 2
    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return 2

    table = {**INSTRUMENTS, **CONVERTIBLE}
    print(f"Verifying {len(table)} instrument specs against the terminal\n")
    head = (f"{'symbol':<9}{'field':<16}{'ours':>14}{'broker':>14}"
            f"{'status':>10}")
    print(head)
    print("-" * len(head))

    mismatches, unavailable = [], []
    for symbol, inst in sorted(table.items()):
        if not mt5.symbol_select(symbol, True):
            unavailable.append(symbol)
            continue
        info = mt5.symbol_info(symbol)
        if info is None:
            unavailable.append(symbol)
            continue

        checks = [
            ("contract_size", inst.contract_size,
             float(getattr(info, "trade_contract_size", 0.0))),
            ("min_lot", inst.min_lot, float(getattr(info, "volume_min", 0.0))),
            ("lot_step", inst.lot_step,
             float(getattr(info, "volume_step", 0.0))),
        ]
        for field, ours, theirs in checks:
            if theirs <= 0:
                continue
            agree = abs(ours - theirs) <= max(1e-9, theirs * 1e-6)
            if not agree:
                mismatches.append((symbol, field, ours, theirs))
                print(f"{symbol:<9}{field:<16}{ours:>14.5f}{theirs:>14.5f}"
                      f"{'MISMATCH':>10}")

        point = float(getattr(info, "point", 0.0))
        if not _pip_is_expressible(inst.pip, point):
            mismatches.append((symbol, "pip", inst.pip, point))
            print(f"{symbol:<9}{'pip/tick':<16}{inst.pip:>14.5f}"
                  f"{point:>14.5f}{'NOT A MULTIPLE':>10}")

    # Quote currency is not an MT5 field, but a nonsensical one is catchable.
    print()
    for symbol, inst in sorted(table.items()):
        derived = quote_currency_of(symbol)
        if derived != inst.quote:
            mismatches.append((symbol, "quote", inst.quote, derived))
            print(f"{symbol:<9}{'quote':<16}{inst.quote:>14}{derived:>14}"
                  f"{'MISMATCH':>10}")
        if len(inst.quote) != 3 or not inst.quote.isalpha():
            mismatches.append((symbol, "quote_shape", inst.quote, "AAA"))
            print(f"{symbol:<9}{'quote_shape':<16}{inst.quote:>14}"
                  f"{'3 letters':>14}{'MISMATCH':>10}")

    mt5.shutdown()

    print("\n" + "=" * 63)
    if mismatches:
        print(f"{len(mismatches)} MISMATCH(ES) -- specs disagree with the broker")
        print("Each of these would produce a plausible but wrong number rather")
        print("than an error. Fix instruments.py before trusting any result.")
    else:
        print("All specs agree with the broker.")
    if unavailable:
        print(f"\nNot offered by this broker ({len(unavailable)}): "
              f"{', '.join(unavailable)}")
        print("Specs for these are unverified -- they may still be wrong.")

    return 1 if (mismatches and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
