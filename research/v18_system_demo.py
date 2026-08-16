"""Show the V18 system deciding what to trade, and why.

Three scenarios, run end to end:

1. The repo's actual measured signals (V15, V16, V17). The system allocates
   nothing and names the failing gate for each.
2. A hypothetical signal that HAS cleared validation, to show the same code
   sizing a position from a measured edge.
3. Risk management under stress: a losing run, a drawdown, and the caps.

Nothing here is a backtest. It exercises the decision logic so the behaviour is
visible rather than described.

    python research/v18_system_demo.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_ai_bridge.enums import Signal as Side  # noqa: E402
from mt5_ai_bridge.risk_v18 import edge_from_trades  # noqa: E402
from mt5_ai_bridge.trading_system import (SignalSpec, TradeIntent,  # noqa: E402
                                          TradingSystem, Validation)

TRIALS = 1379          # every specification tried against this data so far
RULE = "=" * 78


def intent(symbol, side=Side.BUY, entry=1.2000, stop=1.1950,
           pip=0.0001, pip_value=10.0):
    return TradeIntent(symbol=symbol, side=side, entry=entry, stop=stop,
                       pip=pip, pip_value_per_lot=pip_value)


def scenario_1_measured_signals() -> None:
    print(RULE)
    print("1. THE SIGNALS THIS REPOSITORY ACTUALLY MEASURED")
    print(RULE)

    system = TradingSystem(starting_equity=10_000)
    system.register(SignalSpec(
        "V15_momentum", ("XAUUSD",), "H4",
        Validation(out_of_sample_trades=1006, out_of_sample_profit=2217.49,
                   profit_factor=1.082, positive_fold_fraction=0.6,
                   deflated_sharpe=0.0, n_trials=TRIALS,
                   trade_profits=(1.0, -1.0),
                   note="best symbol, after gold's real spread was charged")))
    system.register(SignalSpec(
        "V16_reversion", ("GBPUSD", "EURUSD", "AUDUSD"), "H4",
        Validation(out_of_sample_trades=2202, out_of_sample_profit=-145.48,
                   profit_factor=0.997, positive_fold_fraction=0.2,
                   deflated_sharpe=0.0, n_trials=TRIALS,
                   trade_profits=(1.0, -1.0),
                   note="break-even to three decimals")))
    system.register(SignalSpec(
        "V17_gated", ("GBPUSD", "EURUSD", "AUDUSD"), "H4",
        Validation(out_of_sample_trades=670, out_of_sample_profit=-2835.13,
                   profit_factor=0.826, positive_fold_fraction=0.2,
                   deflated_sharpe=0.0, n_trials=TRIALS,
                   trade_profits=(1.0, -1.0),
                   note="negative even gross")))

    print(system.report())
    print("\nAsking it to trade anyway:")
    for name, symbol in (("V15_momentum", "XAUUSD"),
                         ("V16_reversion", "GBPUSD")):
        plan = system.plan(name, intent(symbol))
        print(f"  {plan.describe()}")
    print("\n  There is no override. The gate is the code path, not a policy.")


def scenario_2_a_validated_signal() -> None:
    print("\n" + RULE)
    print("2. A SIGNAL THAT HAS CLEARED VALIDATION (hypothetical)")
    print(RULE)

    # A track record with a genuine edge: 58% wins at 1.6:1.
    profits = tuple([1.6] * 58 + [-1.0] * 42)
    edge = edge_from_trades(list(profits))
    print(f"  Track record : {edge['trades']} trades, "
          f"{edge['win_rate']:.0%} wins, {edge['win_loss_ratio']:.2f}:1 payoff")
    print(f"  Kelly        : {edge['kelly']:.3f} "
          f"-> quarter Kelly {edge['kelly'] / 4:.3f}, capped at 0.020")

    system = TradingSystem(starting_equity=10_000)
    system.register(SignalSpec(
        "validated_example", ("EURUSD", "GBPUSD", "AUDUSD"), "H4",
        Validation(out_of_sample_trades=640, out_of_sample_profit=5100.0,
                   profit_factor=1.28, positive_fold_fraction=0.8,
                   deflated_sharpe=0.971, n_trials=TRIALS,
                   trade_profits=profits)))
    system.mark(10_000)

    print(f"\n  {system.status().describe()}\n")
    for sym, entry, stop in (("EURUSD", 1.2000, 1.1950),
                             ("GBPUSD", 1.3000, 1.2900),
                             ("AUDUSD", 0.6500, 0.6450)):
        plan = system.plan("validated_example", intent(sym, entry=entry,
                                                       stop=stop))
        used = sum(system.status().open_risk.values())
        print(f"  {plan.describe()}")
        print(f"      USD exposure now {used:.2%} "
              f"(per-currency cap {system.risk.budget.max_currency_risk_fraction:.0%})")
    print("\n  Every one of these is short USD. The per-currency cap treats "
          "them as\n  one bet and stops the third, which is the whole point: "
          "the universe\n  measured here gives only 2.6 effective bets.")


def scenario_3_risk_under_stress() -> None:
    print("\n" + RULE)
    print("3. RISK MANAGEMENT UNDER STRESS")
    print(RULE)

    profits = tuple([1.6] * 58 + [-1.0] * 42)
    spec = SignalSpec(
        "validated_example", ("EURUSD",), "H4",
        Validation(640, 5100.0, 1.28, 0.8, 0.971, TRIALS, profits))

    # -- a slow bleed --
    print("\n  A drawdown accumulating across days:")
    system = TradingSystem(starting_equity=10_000)
    system.register(spec)
    day = datetime(2026, 8, 16, tzinfo=timezone.utc)
    for equity in (10_000, 9_800, 9_500, 9_200, 8_900, 8_400, 8_100):
        system.mark(equity, balance=equity, now=day)
        plan = system.plan("validated_example", intent("EURUSD"))
        dd = system.status().drawdown
        if plan.approved:
            print(f"    equity {equity:>7,}  dd {dd:5.1%}  "
                  f"-> {plan.lots:.2f} lots at {plan.risk_fraction:.2%} risk")
        else:
            print(f"    equity {equity:>7,}  dd {dd:5.1%}  -> {plan.reason}")
        system.record_close("EURUSD", 0.0)
        day += timedelta(days=1)

    # -- a losing streak --
    print("\n  A run of losses inside one day:")
    system2 = TradingSystem(starting_equity=10_000)
    system2.register(spec)
    system2.mark(10_000, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    for i in range(1, 7):
        plan = system2.plan("validated_example", intent("EURUSD"))
        if plan.approved:
            print(f"    loss {i}: sized {plan.lots:.2f} lots")
            system2.record_close("EURUSD", -15.0)
        else:
            print(f"    loss {i}: {plan.reason}")
            break


def main() -> int:
    print("V18 TRADING SYSTEM -- decision logic, not a backtest\n")
    scenario_1_measured_signals()
    scenario_2_a_validated_signal()
    scenario_3_risk_under_stress()

    print("\n" + RULE)
    print("SUMMARY")
    print(RULE)
    print("  The same code allocates nothing to an unproven signal and sizes")
    print("  a proven one from its measured edge. Which happens is decided by")
    print("  evidence, not by configuration, so the system stays correct when")
    print("  your costs change or a new signal is validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
