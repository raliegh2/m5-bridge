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

## Next

1. **News / session filter**; **per-book risk budgets**.
2. **Spread/commission in the backtest** (scalp books will look worse).
3. **Auth before any non-localhost control.**

## Known follow-ups / tech debt

- Backtester does not model spread/commission or the daily/total-loss halt, so
  live results (esp. scalping) will be worse than the backtest.
- Localhost-only dashboard, no auth. Static stops once placed except the trail.

## Note on git

Git can't be run from the assistant's sandbox; commit/push from Windows.
