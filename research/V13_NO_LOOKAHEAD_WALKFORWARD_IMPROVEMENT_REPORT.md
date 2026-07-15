# V13 No-Lookahead Walk-Forward Intraday Improvement Report

Status: **FAILED ROBUST PROFITABILITY GATE — KEEP V11/V13 INTRADAY DISABLED**

## Goal

The goal was to rebuild the intraday extension so it could behave like a real independent system:

- use only information available at the signal bar;
- enter on the next bar, not on the same bar after knowing the candle result;
- optimize only on past training data;
- validate on a later confirmation segment;
- trade the following out-of-sample year only if the training and confirmation results both passed;
- keep trades intraday only with no overnight holds;
- use quality filters for trend, volatility, volume, spread, and setup structure.

## Data used

Uploaded M5 data for:

| Symbol | Period available |
|---|---|
| EURUSD | 2016-01-04 to 2026-07-03 |
| GBPUSD | 2016-01-04 to 2026-07-03 |
| GBPJPY | 2016-01-04 to 2026-07-03 |

Backtest window: **2016-07-03 to 2026-07-03**.

## Strategy families researched and implemented

The system tested multiple robust intraday families instead of trying to force the previous losing proxy:

1. **Trend continuation / Donchian breakout**
   - H1 EMA trend filter
   - H1 ADX trend-strength filter
   - M5 range breakout
   - MACD confirmation
   - London/New York session filter

2. **Trend pullback / reclaim**
   - H1 trend filter
   - EMA 20 pullback
   - EMA 9 reclaim
   - MACD confirmation
   - body-quality filter

3. **False-breakout / reversal variant**
   - Same structural conditions as the trend/fade families
   - Reverse-direction variants included only if prior data validated them

4. **Mean-reversion fade filter**
   - Low-trend ADX filter
   - ATR-normalized overextension from EMA 50
   - direction confirmation before entry

## No-lookahead validation method

For each symbol and test year:

1. Use only the prior two years as training data.
2. Split the two-year training period into development and confirmation halves.
3. A setup is allowed only if both halves are profitable and pass the profit-factor gate.
4. Trade the next calendar year out-of-sample with the selected setup.
5. If no setup passes, take no trades for that symbol/year.

This prevents choosing a setup because of future test-year results.

## Final out-of-sample result

| Metric | Value |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $4,797.95 |
| Net profit | **-$202.05** |
| Return | **-4.04%** |
| Trades | 214 |
| Wins | 120 |
| Losses | 94 |
| Win rate | 56.07% |
| Profit factor | 0.770 |
| Max drawdown | 5.06% |
| Average trade | -$0.94 |

## Profit by symbol

| Symbol | Trades | Net profit | PF | Win rate | Avg R |
|---|---:|---:|---:|---:|---:|
| EURUSD | 214 | -$204.99 | 0.771 | 56.07% | -0.1064R |
| GBPUSD | 0 | $0.00 | N/A | N/A | N/A |
| GBPJPY | 0 | $0.00 | N/A | N/A | N/A |

## Yearly result

| Year | Trades | Net profit | PF | Win rate |
|---:|---:|---:|---:|---:|
| 2024 | 214 | -$204.99 | 0.771 | 56.07% |

All other symbol/year combinations were blocked because no setup passed the past-only robustness gate.

## Decision

The rebuilt no-lookahead walk-forward system did **not** become profitable on the uploaded 10-year data. The correct decision is therefore:

> Do **not** promote the V11/V13 intraday extension into the V12 Final strategy yet.

The safety system worked by blocking most weak setups, but the one admitted EURUSD setup failed out-of-sample. The safest production behavior is to leave the V12 Final profile active and keep the V11/V13 intraday extension disabled until a genuinely robust signal generator is proven.

## What this means for the previous independent test

The earlier independent positive result cannot be replicated from the current branch using only the reconstructed rules. To replicate it properly, one of the following is required:

1. the exact original V11 signal-generation code;
2. the exact original accepted/rejected V11 candidate ledger;
3. a broker-native Strategy Tester export showing every signal, rejected candidate, and fill assumption.

Without that, forcing the uploaded data to match the previous profit would create lookahead/overfit risk.

## Recommended next improvement path

1. Export the true original V11 candidate ledger if it exists.
2. Replay that ledger against the uploaded M5/M1 data.
3. Add a live adaptive guard that stops any intraday engine after a rolling drawdown or PF failure.
4. Keep V11/V13 intraday disabled until it beats the V12 Final baseline in a proper chronological replay.
