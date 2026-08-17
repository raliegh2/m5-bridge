# Roadmap

## Done

- **Core trading loop**, **foundation refactor**, **logging**, **SQLite journaling**.
- **Backtesting** (single strategy) + CSV/MT5 loaders + CLI.
- **Rule-based reasoning layer** (confluence + RSI veto).
- **Hardening** — realised daily-loss tracker, resilient reconnect loop.
- **Automated execution** — session sizing, intraday/swing style, daily cap.
- **Live dashboard** — P/L, R:R, EST clock, session, per-position pips.
- **Pyramiding + staggered exits**, **minimum-3 burst on strong trends**.
- **Trailing stop** + **cap raised to 7** active on high momentum.
- **Display-only website + console start** (Run Bot.bat); **Close All Trades.bat**.
- **Multi-timeframe books** — swing H4/D1 (aligned-only, stack on strong),
  NY-only day-trade M15 + scalp M5 on strong momentum, magic-tracked, bounded.
- **Multi-book backtester + history exporter** (this change):
  - `backtest_books.py` simulates a broker (resampled H4/D1/M15/M5, concurrent
    positions, SL/TP fills, trailing) and replays the LIVE `_run_books` code.
  - Per-book + overall stats, equity curve, max drawdown.
    `python -m mt5_ai_bridge.backtest_books <m5.csv>`.
  - `export_history.py` / **Export History.bat** pulls M5 bars from MT5 to a CSV.
  - Suite now 116 tests, all green.
  - Demo finding: swing books profitable in trend; M5/M15 scalp/day books bleed
    on noise — consider DAY_STRONG_MAX=0 / SCALP_STRONG_MAX=0 unless real-data
    backtests say otherwise.

- **Honest measurement (V15)** — see `research/V15_EDGE_INVESTIGATION.md`:
  - `costs.py` — tested spread/slippage/commission/swap model, wired into
    `backtest.py`; the CLI now defaults to realistic costs. Confirmed the
    predicted effect: scalp books look worse, as they should.
  - `validation.py` — walk-forward splits, deflated Sharpe, BH-FDR and explicit
    PASS/FAIL gates. `research/honest_walk_forward.py` scores the strategy out
    of sample and deflates by the number of parameter sets tried.
  - `entry_diagnostics.py` — every blocking gate for an entry, not just the
    first, plus a session-wide rejection ledger and a CLI.
  - `candidate_v15.py` — a pre-registered time-series-momentum candidate with
    parameters frozen in `research/v15_locked_candidate.json`.

## Next

1. **Export multi-year history for every traded symbol.** This is now the
   binding constraint: eight months of GBPUSD M5 is the only committed data, so
   no edge claim in `research/` can be reproduced or tested.
2. **News / session filter**; **per-book risk budgets**.
3. **Auth before any non-localhost control.**

## Known follow-ups / tech debt

- Backtester does not model the daily/total-loss halt (costs are now modelled).
- Localhost-only dashboard, no auth. Static stops once placed except the trail.
- The 10-year results throughout `research/` reference data that is not
  committed; treat them as unverified until the history is restored.

## Note on git

Git can't be run from the assistant's sandbox; commit/push from Windows.
