"""Cross-sectional momentum on the tradable equity universe.

The account can order 11,438 individual equities and no cross-sectional test
has ever been run against them. This runs one: the published 12-1 momentum
specification, market-neutral, judged by the repo's existing gates.

Market-neutral matters here. The ETF result failed because a long-biased rule
in a rising asset earns a profit factor without adding anything, and holding
beat it six times out of six. A book that is long 20 names and short 20 names
in equal weight cannot earn its return that way -- the market's direction
cancels, so zero really is the benchmark.

    python research/cross_sectional_test.py --json-out research/cross_sectional.json
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
from mt5_ai_bridge.cross_sectional import (build_panel,  # noqa: E402
                                           locked_config,
                                           replay_cross_sectional)
from mt5_ai_bridge.validation import (DEFAULT_GATES, deflated_sharpe_ratio,
                                      evaluate)  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research" / "data" / "equities"
MANIFEST = ROOT / "research" / "equity_universe.json"
PERIODS_PER_YEAR = 12.0


def load_universe(limit=None) -> tuple[dict, dict]:
    """Split-adjusted bars and per-symbol spread, from the exported universe."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    spreads, bars, splits_fixed = {}, {}, 0
    rows = manifest["symbols"][:limit] if limit else manifest["symbols"]
    for row in rows:
        path = DATA / f"{row['symbol']}_D1.csv"
        if not path.exists():
            continue
        frame, events = adjust_for_splits(pd.read_csv(path))
        splits_fixed += len(events)
        bars[row["symbol"]] = frame
        spreads[row["symbol"]] = float(row["spread_pct"])
    print(f"  loaded {len(bars)} symbols, back-adjusted {splits_fixed} splits")
    return bars, spreads


def metrics_from(result, n_trials: int) -> dict:
    returns = result.period_returns
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    annual = result.annualised(PERIODS_PER_YEAR)
    return {
        "periods": result.periods,
        "trades": result.periods * 2 * locked_config().n_positions,
        "net_profit": result.net_profit,
        "return_percent": result.return_percent,
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
        "hit_rate": result.hit_rate,
        "annual_return_pct": annual["return_pct"],
        "annual_volatility_pct": annual["volatility_pct"],
        "sharpe": annual["sharpe"],
        "max_drawdown_percent": result.max_drawdown_percent,
        "mean_cost_drag_per_period_pct": round(
            float(np.mean(result.cost_drag)) * 100.0, 4) if result.cost_drag else 0.0,
        "deflated_sharpe": round(float(deflated_sharpe_ratio(
            returns, n_trials, PERIODS_PER_YEAR)), 4) if len(returns) > 2 else 0.0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--balance", type=float, default=4_802.43)
    parser.add_argument("--trials", type=int, default=1,
                        help="trials to deflate against; the spec is one")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    cfg = locked_config()
    print("=" * 88)
    print("CROSS-SECTIONAL MOMENTUM -- 12-1, market-neutral, one locked trial")
    print("=" * 88)
    if not MANIFEST.exists():
        print("No universe. Run tools/export_equity_universe.py first.")
        return 2

    bars, spreads = load_universe(args.limit)
    if len(bars) < cfg.min_names:
        print(f"Only {len(bars)} symbols; need {cfg.min_names} to rank.")
        return 2

    panel = build_panel(bars)
    span = pd.to_datetime([panel.index[0], panel.index[-1]], unit="s", utc=True)
    print(f"  panel {panel.shape[0]} days x {panel.shape[1]} names, "
          f"{span[0]:%Y-%m-%d}..{span[1]:%Y-%m-%d}")
    print(f"  median spread {np.median(list(spreads.values())):.3f}% "
          f"| rebalance every {cfg.holding_days}d, "
          f"{cfg.n_positions} long / {cfg.n_positions} short")

    payload = {"universe": len(bars), "days": int(panel.shape[0]),
               "config": {"lookback_days": cfg.lookback_days,
                          "skip_days": cfg.skip_days,
                          "holding_days": cfg.holding_days,
                          "n_positions": cfg.n_positions},
               "median_spread_pct": round(
                   float(np.median(list(spreads.values()))), 4)}

    print("\n" + "=" * 88)
    print("FULL SAMPLE")
    print("=" * 88)
    gross = replay_cross_sectional(panel, cfg, None, args.balance)
    net = replay_cross_sectional(panel, cfg, spreads, args.balance)
    for label, result in (("gross (no costs)", gross), ("net of spread", net)):
        m = metrics_from(result, args.trials)
        print(f"\n{label}: {m['periods']} periods  "
              f"return {m['return_percent']:+.2f}%  "
              f"annual {m['annual_return_pct']:+.2f}%  "
              f"Sharpe {m['sharpe']}  PF {m['profit_factor']}  "
              f"maxDD {m['max_drawdown_percent']}%")
        print(f"    hit rate {m['hit_rate']:.1%}  "
              f"cost drag {m['mean_cost_drag_per_period_pct']:.3f}%/period")
        payload[label.split()[0]] = m

    long_mean = float(np.mean(net.long_returns)) * 100 if net.long_returns else 0
    short_mean = float(np.mean(net.short_returns)) * 100 if net.short_returns else 0
    print(f"\n  long leg  {long_mean:+.3f}%/period")
    print(f"  short leg {short_mean:+.3f}%/period  "
          "(sign already flipped: positive means shorting losers paid)")
    payload["long_leg_mean_pct"] = round(long_mean, 4)
    payload["short_leg_mean_pct"] = round(short_mean, 4)

    print("\n" + "=" * 88)
    print(f"WALK-FORWARD ({args.folds} folds, net of costs)")
    print("=" * 88)
    edges = np.linspace(cfg.lookback_days + 1, panel.shape[0],
                        args.folds + 1, dtype=int)
    fold_returns, folds = [], []
    for k in range(args.folds):
        fold = replay_cross_sectional(panel, cfg, spreads, args.balance,
                                      start_index=int(edges[k]),
                                      end_index=int(edges[k + 1]))
        window = pd.to_datetime(
            [panel.index[edges[k]], panel.index[edges[k + 1] - 1]],
            unit="s", utc=True)
        fold_returns.extend(fold.period_returns)
        folds.append({"fold": k, "periods": fold.periods,
                      "return_percent": fold.return_percent,
                      "sharpe": fold.annualised(PERIODS_PER_YEAR)["sharpe"]})
        print(f"  fold {k}  {window[0]:%Y-%m}..{window[1]:%Y-%m}  "
              f"{fold.periods:>3} periods  return {fold.return_percent:>+8.2f}%  "
              f"Sharpe {folds[-1]['sharpe']:>6.2f}")

    positive = sum(1 for f in folds if f["return_percent"] > 0)
    payload["folds"] = folds
    payload["positive_fold_fraction"] = round(positive / len(folds), 3)

    print("\n" + "=" * 88)
    print("GATES")
    print("=" * 88)
    m = metrics_from(net, args.trials)
    m["positive_fold_fraction"] = payload["positive_fold_fraction"]
    verdict = evaluate(m, DEFAULT_GATES)
    for gate in DEFAULT_GATES:
        mark = "PASS" if gate.check(m) else "FAIL"
        print(f"  [{mark}] {gate.name}: {gate.describe}")
    print(f"\n  {verdict.explain()}")
    payload["metrics"] = m
    payload["passed"] = verdict.passed
    payload["failed_gates"] = list(verdict.failed_gates)

    # Deflating against every trial the whole investigation has run is the
    # conservative reading; this spec came from the literature, not a search.
    conservative = round(float(deflated_sharpe_ratio(
        net.period_returns, 1389, PERIODS_PER_YEAR)), 4)
    print(f"\n  deflated Sharpe vs 1 trial (this spec)      : "
          f"{m['deflated_sharpe']}")
    print(f"  deflated Sharpe vs 1,389 trials (whole repo): {conservative}")
    payload["deflated_sharpe_all_trials"] = conservative

    print("\n" + "=" * 88)
    print(f"FEASIBILITY ON ${args.balance:,.2f}")
    print("=" * 88)
    last_prices = panel.iloc[-1].dropna()
    needed = 2 * cfg.n_positions
    cheapest = last_prices.nsmallest(needed)
    notional = float(cheapest.sum())
    print(f"  {needed} positions at one share each, cheapest names: "
          f"${notional:,.2f} notional")
    print(f"  that is {notional / args.balance:.1f}x the account balance")
    payload["feasibility"] = {
        "positions_required": needed,
        "min_notional_one_share_each": round(notional, 2),
        "times_balance": round(notional / args.balance, 2),
    }

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2),
                                       encoding="utf-8")
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
