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

- **The tactical book (current model)** — `tactical_bot.py`,
  `tactical_allocation.py`, `tactical_runner.py`:
  - Faber (2007) ten-month moving average, long-or-flat, monthly, on two
    sleeves: SCHX (US large cap) and IAU (gold). Parameters frozen in
    `research/tactical_locked.json` and checked against the code at load.
  - 6.35% a year with a 7.7% worst rolling ten-year drawdown at 70% invested;
    $5,000 -> a median $7,528 over ten years. See
    `research/FIVE_K_TEN_YEAR.md`.
  - Wired to the live risk system: DrawdownGovernor scales exposure, KillSwitch
    flattens, whole-share targets, magic-scoped positions, exits before entries.
  - Honest limits: this is beta at a controlled risk level, the Sharpe gain over
    buy-and-hold is not significant (p = 0.29), and half the book is in gold
    after an exceptional decade for gold.

- **What was measured and rejected** — the reason the model is this modest:
  - ETF mean reversion (`research/ETF_PORTFOLIO.md`) — beaten by buy-and-hold on
    6 of 6 once unadjusted splits were corrected; the two best results (TQQQ PF
    1.512, ONEQ PF 1.301) turned negative.
  - Cross-sectional equity momentum (`research/CROSS_SECTIONAL_RESULT.md`) —
    -88.5% over 22.9 years; benchmarked against the universe it adds +1.44% a
    year at t = 0.815. Blocked by a survivors-only universe, not by the code.
  - `corporate_actions.py` — split detection and back-adjustment; `data_audit`
    now rates an unadjusted split fatal rather than a minor outlier.
  - `risk_v18.exposure_groups` — equity tickers are no longer sliced as if they
    were currency pairs, and a hedged book can cap net rather than gross factor
    exposure.

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
