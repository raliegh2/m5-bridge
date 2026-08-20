"""CLI for backtesting:  python -m mt5_ai_bridge <csv> [options]

Examples:
    python -m mt5_ai_bridge data/GBPUSD_M30.csv --sl 30 --tp 60
    python -m mt5_ai_bridge data/GBPUSD_M30.csv --strategy reasoning --threshold 0.6
"""

import argparse
import json
import sys

from .backtest import Backtester
from .costs import PRESETS, CostModel, preset
from .data import load_csv
from .reasoning import ReasoningConfig, ReasoningStrategy
from .strategy import evaluate_strategy


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mt5_ai_bridge",
                                description="Backtest the strategy on OHLC CSV data.")
    p.add_argument("csv", help="Path to an OHLC CSV file")
    p.add_argument("--strategy", choices=("trend", "reasoning"), default="trend",
                   help="Decision function to backtest")
    p.add_argument("--threshold", type=float, default=0.6,
                   help="Confidence threshold for the reasoning strategy")
    p.add_argument("--pip", type=float, default=0.0001, help="Pip size (default 0.0001)")
    p.add_argument("--lot", type=float, default=0.01, help="Lot size")
    p.add_argument("--sl", type=float, default=30, help="Stop-loss pips")
    p.add_argument("--tp", type=float, default=60, help="Take-profit pips")
    p.add_argument("--balance", type=float, default=10_000, help="Starting balance")
    p.add_argument("--contract", type=float, default=100_000, help="Contract size")
    p.add_argument("--trades", action="store_true", help="Print each trade")
    p.add_argument("--cost", choices=sorted(PRESETS), default="typical",
                   help="Broker cost preset (default: typical retail). "
                        "'zero' reproduces the old gross replay.")
    p.add_argument("--spread", type=float, default=None,
                   help="Override spread in pips")
    p.add_argument("--slippage", type=float, default=None,
                   help="Override per-side slippage in pips")
    p.add_argument("--commission", type=float, default=None,
                   help="Override commission in $ per lot per round turn")
    args = p.parse_args(argv)

    cost = preset(args.cost)
    overrides = {}
    if args.spread is not None:
        overrides["spread_pips"] = args.spread
    if args.slippage is not None:
        overrides["slippage_pips"] = args.slippage
    if args.commission is not None:
        overrides["commission_per_lot_round_turn"] = args.commission
    if overrides:
        cost = CostModel(**{**vars(cost), **overrides})

    if args.strategy == "reasoning":
        strategy_fn = ReasoningStrategy(ReasoningConfig(threshold=args.threshold))
    else:
        strategy_fn = evaluate_strategy

    df = load_csv(args.csv)
    bt = Backtester(pip_size=args.pip, lot_size=args.lot, stop_loss_pips=args.sl,
                    take_profit_pips=args.tp, contract_size=args.contract,
                    starting_balance=args.balance, strategy_fn=strategy_fn,
                    cost=cost)
    result = bt.run(df)

    print(f"Backtest: {args.csv}  ({len(df)} bars)  strategy={args.strategy}")
    print(f"Costs: preset={args.cost} spread={cost.spread_pips}p "
          f"slippage={cost.slippage_pips}p/side "
          f"commission=${cost.commission_per_lot_round_turn}/lot  "
          f"(round trip {cost.round_trip_pips:.2f}p)")
    print(json.dumps(result.summary(), indent=2))
    if result.n_trades and result.gross_profit > 0 >= result.total_profit:
        print("\nNOTE: this strategy is profitable gross and unprofitable net. "
              "The edge is smaller than the cost of trading it.")

    if args.trades:
        print("\nTrades:")
        for t in result.trades:
            print(f"  {t.entry_time} {t.side.value:4} @ {t.entry_price:.5f} -> "
                  f"{t.exit_price:.5f} [{t.exit_reason}] "
                  f"{t.pips:+.1f} pips  {t.profit:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
