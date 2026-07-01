"""CLI for backtesting:  python -m mt5_ai_bridge <csv> [options]

Examples:
    python -m mt5_ai_bridge data/GBPUSD_M30.csv --sl 30 --tp 60
    python -m mt5_ai_bridge data/GBPUSD_M30.csv --strategy reasoning --threshold 0.6
"""

import argparse
import json
import sys

from .backtest import Backtester
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
    args = p.parse_args(argv)

    if args.strategy == "reasoning":
        strategy_fn = ReasoningStrategy(ReasoningConfig(threshold=args.threshold))
    else:
        strategy_fn = evaluate_strategy

    df = load_csv(args.csv)
    bt = Backtester(pip_size=args.pip, lot_size=args.lot, stop_loss_pips=args.sl,
                    take_profit_pips=args.tp, contract_size=args.contract,
                    starting_balance=args.balance, strategy_fn=strategy_fn)
    result = bt.run(df)

    print(f"Backtest: {args.csv}  ({len(df)} bars)  strategy={args.strategy}")
    print(json.dumps(result.summary(), indent=2))

    if args.trades:
        print("\nTrades:")
        for t in result.trades:
            print(f"  {t.entry_time} {t.side.value:4} @ {t.entry_price:.5f} -> "
                  f"{t.exit_price:.5f} [{t.exit_reason}] "
                  f"{t.pips:+.1f} pips  {t.profit:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
