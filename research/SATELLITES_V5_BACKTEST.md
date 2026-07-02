# Satellite V5 evidence-gate backtest

## Scope and methodology

The V5 quality gates were replayed against the completed V4 trade records and
the matching raw M15 feature state. This preserves the V4 execution outcomes
while testing whether the new gates would have removed weak trades.

This is a **quality-gate replay**, not a complete signal re-simulation. When a
historical V4 trade is removed, the replay does not reconstruct any additional
signal that might have become available while that removed trade would otherwise
have been open. The results are useful for screening, but a fresh end-to-end MT5
backtest and forward demo are still required before promotion.

The supplied EURUSD and GBPJPY M15 histories start in June 2022, so no honest
10-year M15 result is available.

## EURUSD V5 gate

Enabled gate:

- existing EURUSD V4 long pullback-resumption signal
- entry time from 10:00 through 10:59 UTC
- completed H1 candle range no greater than 1.35 H1 ATR
- risk reduced from 0.15% to 0.10%
- Asian false-break and resistance-space experiments remain configurable but
  are disabled by default

### Replay results

| Window | Net | Trades | Trades/week | PF | Win rate | Closed-trade DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$35.96 | 30 | 0.16 | 1.60 | 66.67% | 0.34% |
| 3 years | +$30.24 | 25 | 0.16 | 1.62 | 68.00% | 0.34% |
| 2 years | +$30.77 | 15 | 0.14 | 2.26 | 73.33% | 0.13% |
| 1 year | +$8.49 | 7 | 0.13 | 1.71 | 71.43% | 0.12% |
| 1 month | $0.00 | 0 | 0.00 | n/a | n/a | 0.00% |

Development/validation split:

- through 2024-07-01: +$5.18, 15 trades, PF 1.15
- from 2024-07-02: +$30.77, 15 trades, PF 2.26

The gate improves the historical V4 trade set from marginal performance to a
positive replay. Frequency remains very low and the development PF is only 1.15,
so EURUSD remains **READ_ONLY research**, not production-ready.

### Rejected optional EURUSD family

The Asian false-break prototype produced eight trades and **-$31.94**. It is
implemented behind a disabled flag and must not be enabled without a new,
independent validation.

## GBPJPY V5 gate

Enabled gate:

- existing profitable GBPJPY V4 long breakout-retest signal
- entry time from 08:00 through 09:59 UTC
- standard V4 volatility regime retained for the live candidate
- risk remains 0.10%

### Replay results

| Window | Net | Trades | Trades/week | PF | Win rate | Closed-trade DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$114.41 | 21 | 0.11 | 6.04 | 80.95% | 0.12% |
| 3 years | +$87.18 | 16 | 0.10 | 6.05 | 81.25% | 0.12% |
| 2 years | +$43.73 | 8 | 0.08 | 4.87 | 75.00% | 0.11% |
| 1 year | +$25.24 | 4 | 0.08 | 5.37 | 75.00% | 0.11% |
| 1 month | $0.00 | 0 | 0.00 | n/a | n/a | 0.00% |

Development/validation split:

- through 2024-07-01: +$70.67, 13 trades, PF 7.20
- from 2024-07-02: +$43.73, 8 trades, PF 4.87

The 08:00-10:00 gate removed weak hours while preserving most of the historical
profit. The sample remains only 21 trades, so GBPJPY V5 is an
**approval-mode/forward-demo candidate**, not an unattended live strategy.

### Rejected optional GBPJPY families

- ADX deep-pullback prototype: three trades, **-$13.61**
- lowered 55%-65% ATR tier: three observed prototype trades, **-$2.42**

Both are implemented behind disabled flags. The standard >=65% volatility tier
remains the default because the requested reduction to 55% did not improve the
supplied sample.

## Comparison with GBPUSD Satellite V2

The GBPUSD satellite previously generated approximately +$358.54 in its
available one-year test. The V5 replays do not match that dollar result:

- EURUSD V5 latest year: +$8.49
- GBPJPY V5 latest year: +$25.24

Their PF and drawdown are attractive after gating, especially GBPJPY, but the
trade frequency is too low to generate comparable dollars at the current safe
risk. Increasing lot size to force equal profit is not justified by these small
samples.

## Decision

- GBPUSD V4 Swing: unchanged
- GBPUSD Satellite V2: unchanged research/demo status
- EURUSD V5: READ_ONLY research; optional false-break disabled
- GBPJPY V5: approval-mode/forward-demo candidate; lower-ATR and deep-pullback
  families disabled
- branch remains unmerged until fresh end-to-end backtests, local tests and at
  least 30 forward-demo trades per candidate are completed
