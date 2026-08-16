"""Multi-symbol portfolio test with walk-forward symbol admission.

The question this answers is not "can I find a profitable combination" -- with
enough combinations you always can. It is: **if I had been choosing symbols as I
went, using only what I knew at the time, what would I have earned?**

Each fold admits only the symbols that were profitable across the folds before
it, optionally also requiring measured trend persistence. Fold 0 admits
everything, because at that point nothing is known.

Run (after tools/export_validation_history.py):
    python research/v15_portfolio_test.py
    python research/v15_portfolio_test.py --require-persistence
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_ai_bridge.candidate_v15 import locked_config, replay  # noqa: E402
from mt5_ai_bridge.costs import preset  # noqa: E402
from mt5_ai_bridge.data import load_csv  # noqa: E402
from mt5_ai_bridge.instruments import instrument_for  # noqa: E402
from mt5_ai_bridge.persistence import log_returns, variance_ratio  # noqa: E402
from mt5_ai_bridge.portfolio_v15 import (PortfolioConfig,  # noqa: E402
                                         diversification_report,
                                         replay_portfolio)
from mt5_ai_bridge.validation import (FoldResult, TrialRegistry,  # noqa: E402
                                      WalkForwardReport, evaluate,
                                      sharpe_ratio, walk_forward_splits)

DATA_DIR = Path(__file__).resolve().parents[1] / "research" / "data"
REGISTRY = Path(__file__).resolve().parents[1] / "research" / "v15_trials.json"


def load_symbols(timeframe: str, since: int | None):
    """Load every priceable symbol, skipping what instruments.py refuses."""
    out, skipped = {}, {}
    for path in sorted(DATA_DIR.glob(f"*_{timeframe}.csv")):
        symbol = path.stem.rsplit("_", 1)[0]
        try:
            instrument_for(symbol)
        except ValueError as exc:
            skipped[symbol] = str(exc).split(".")[0]
            continue
        df = load_csv(str(path)).reset_index(drop=True)
        if since:
            df = df[df["time"] >= since].reset_index(drop=True)
        out[symbol] = df
    return out, skipped


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframe", default="H4")
    p.add_argument("--cost", default="typical")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--since", type=int, default=915148800,
                   help="Drop bars before this epoch (default: 1999-01-01, "
                        "which removes MetaQuotes' synthetic pre-euro bars)")
    p.add_argument("--require-persistence", action="store_true",
                   help="Admit a symbol only if its variance ratio shows "
                        "significant trending on the folds seen so far")
    p.add_argument("--json-out", default=None)
    args = p.parse_args(argv)

    cfg = locked_config()
    cost = preset(args.cost)
    pcfg = PortfolioConfig()

    bars, skipped = load_symbols(args.timeframe, args.since)
    if not bars:
        print(f"No priceable {args.timeframe} data in {DATA_DIR}.")
        return 2

    # Every symbol/config pair examined is a trial. Recording them here means
    # the deflation count cannot quietly reset between sessions.
    registry = TrialRegistry(REGISTRY)
    registry.record_many(
        [{"candidate": "V15", "symbol": s, "timeframe": args.timeframe,
          "portfolio": True} for s in bars], label="portfolio")

    print(f"Portfolio V15 | {len(bars)} symbols | {args.timeframe} | "
          f"cost={args.cost}")
    print(f"Risk: {pcfg.risk_percent_per_trade}%/trade, "
          f"{pcfg.max_total_risk_percent}% total, "
          f"{pcfg.max_currency_risk_percent}%/currency, "
          f"max {pcfg.max_concurrent_positions} open")
    if args.since:
        print(f"Bars from epoch {args.since} onward (synthetic history dropped)")
    if skipped:
        print(f"Refused (not priceable): {', '.join(skipped)}")

    # How much diversification is actually on offer, before any P&L.
    div = diversification_report(bars)
    print(f"\nDiversification: {div['effective_bets']} effective bets from "
          f"{div['n_symbols']} symbols "
          f"(mean |rho| = {div['mean_abs_correlation']})")
    print(f"  Sharpe multiplier x{div['sharpe_multiplier']} "
          f"(x{div['n_symbols'] ** 0.5:.2f} if they were independent)")
    print(f"  Quote currencies: {div['shared_quote_currencies']}")
    print("  NOTE: this multiplies an edge that already exists. It cannot "
          "create one.")
    print()

    # Folds are built on the shortest series so every symbol spans them.
    n_bars = min(len(df) for df in bars.values())
    splits = walk_forward_splits(n_bars, n_folds=args.folds, train_frac=0.6,
                                 embargo=cfg.entry_lookback)

    folds = []
    admitted_log = {}
    per_symbol_history: dict[str, list[float]] = {s: [] for s in bars}

    for split in splits:
        # --- admission decided ONLY from earlier folds -------------------
        if split.index == 0:
            admitted = sorted(bars)          # nothing known yet
            reason = "fold 0: nothing known yet, all symbols admitted"
        else:
            admitted = sorted(
                s for s, hist in per_symbol_history.items()
                if hist and sum(hist) > 0)
            reason = f"profitable across folds 0..{split.index - 1}"
            if args.require_persistence:
                keep = []
                for s in admitted:
                    prior = bars[s].iloc[:split.test_start]
                    try:
                        vr = variance_ratio(
                            log_returns(prior["close"].tolist()), q=6)
                        if vr.trending:
                            keep.append(s)
                    except ValueError:
                        continue
                admitted = keep
                reason += " and significantly trending"

        admitted_log[split.index] = {"symbols": admitted, "reason": reason}

        if not admitted:
            folds.append(FoldResult(split=split, net_profit=0.0, trades=0,
                                    returns=[]))
            print(f"  fold {split.index}: NO SYMBOLS ADMITTED ({reason})")
            continue

        slice_ = {s: bars[s].iloc[split.test_slice()].reset_index(drop=True)
                  for s in admitted}
        result = replay_portfolio(slice_, cfg, pcfg, cost)
        folds.append(FoldResult(split=split, net_profit=result.net_profit,
                                trades=len(result.trades),
                                returns=result.returns))

        # Record each symbol's contribution for the NEXT fold's admission.
        contributions = result.by_symbol()
        for s in bars:
            per_symbol_history[s].append(
                contributions.get(s, {}).get("profit", 0.0))

        print(f"  fold {split.index}: net={result.net_profit:+10.2f}  "
              f"trades={len(result.trades):4d}  dd={result.max_drawdown_percent:5.2f}%  "
              f"admitted={len(admitted)} [{', '.join(admitted)}]")

    n_trials = registry.count
    report = WalkForwardReport(
        folds=folds, n_trials=n_trials,
        trial_sharpes=[sharpe_ratio(f.returns, 1.0) for f in folds if f.returns])
    metrics = report.metrics()
    verdict = evaluate(metrics)

    print("\n  metrics:")
    for k, v in metrics.items():
        print(f"    {k:<24}{v}")
    print(f"    {'trials on record':<24}{n_trials}")

    print("\n" + "=" * 64)
    print("PORTFOLIO VERDICT")
    print("=" * 64)
    print(verdict.explain())

    print("\nAdmission history (decided without lookahead):")
    for idx, rec in admitted_log.items():
        print(f"  fold {idx}: {rec['reason']}")
        print(f"           -> {', '.join(rec['symbols']) or '(none)'}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "timeframe": args.timeframe, "cost": args.cost,
            "since": args.since, "n_trials": n_trials,
            "require_persistence": args.require_persistence,
            "skipped": skipped,
            "diversification": div,
            "metrics": metrics,
            "verdict": {"passed": verdict.passed,
                        "failed_gates": list(verdict.failed_gates)},
            "folds": [{"index": f.split.index, "net_profit": f.net_profit,
                       "trades": f.trades} for f in folds],
            "admission": admitted_log,
        }, indent=2))
        print(f"\nWrote {args.json_out}")

    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
