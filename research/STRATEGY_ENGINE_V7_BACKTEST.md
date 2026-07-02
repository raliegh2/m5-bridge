# Strategy Engine V7 backtest

## Implemented structure

- GBPUSD V4 Swing core: unchanged at 0.35% risk
- GBPUSD Swing V5 add-on: H4 trend pullback at 0.20% risk
- GBPUSD Satellite V2: unchanged at 0.25% risk
- EURUSD Satellite V7: three pair-specific M15 setup families at 0.25% risk
- GBPJPY Satellite V7: two pair-specific M15 setup families at 0.25% risk

All new components remain disabled by default. EURUSD is READ_ONLY research and
GBPJPY requires approval/forward-demo mode.

## Why exact GBPUSD-rule copying was rejected

A direct port of GBPUSD Satellite V2 thresholds generated hundreds of EURUSD and
GBPJPY trades but produced profit factors below 1.0. V7 therefore copies the
multi-setup architecture, not the exact pair parameters.

## Satellite execution assumptions

- next-M15-bar entry
- one open position per symbol
- EURUSD: maximum one accepted entry per day
- GBPJPY: maximum two accepted entries per day
- 0.25% risk per trade
- break-even after 1R
- forced flat at 20:00 UTC
- EURUSD spread floor 0.8 pip and 0.3 pip slippage
- GBPJPY spread floor 1.5 pips and 0.6 pip slippage
- 25% execution-cost stress test

No complete historical event-calendar file was supplied. M15 data begins in June
2022, so no honest ten-year satellite result is available.

## EURUSD Satellite V7

Enabled setups:

1. EUR_COMPRESSION_LONG
2. EUR_MOMENTUM_SHORT
3. EUR_NY_RETEST_SHORT

| Window | Net | Trades | PF | Win rate | Max DD |
|---|---:|---:|---:|---:|---:|
| Full available | +$523.22 | 95 | 2.08 | 46.32% | 0.97% |
| Development through 2024-07 | +$239.47 | 51 | 2.05 | 41.18% | 0.97% |
| Validation from 2024-07 | +$269.55 | 44 | 2.10 | 52.27% | 0.85% |
| Latest year | +$156.40 | 25 | 2.21 | 52.00% | 0.78% |
| Latest month | +$26.48 | 3 | 2.80 | 66.67% | 0.36% |
| Full cost stress +25% | +$475.50 | 95 | 1.93 | 46.32% | 1.20% |

Full-sample setup contribution:

| Setup | Trades | Net |
|---|---:|---:|
| EUR_COMPRESSION_LONG | 19 | +$155.01 |
| EUR_MOMENTUM_SHORT | 54 | +$163.69 |
| EUR_NY_RETEST_SHORT | 22 | +$204.52 |

## GBPJPY Satellite V7

Enabled setups:

1. GJ_MOMENTUM_LONG
2. GJ_PULLBACK_SHORT

| Window | Net | Trades | PF | Win rate | Max DD |
|---|---:|---:|---:|---:|---:|
| Full available | +$459.36 | 86 | 2.36 | 43.02% | 2.18% |
| Development through 2024-07 | +$276.03 | 49 | 2.50 | 42.86% | 1.15% |
| Validation from 2024-07 | +$168.09 | 37 | 2.18 | 43.24% | 2.30% |
| Latest year | +$129.33 | 19 | 2.93 | 52.63% | 2.32% |
| Latest month | +$9.56 | 2 | 1.77 | 50.00% | 0.29% |
| Full cost stress +25% | +$410.91 | 86 | 2.16 | 41.86% | 2.20% |

Full-sample setup contribution:

| Setup | Trades | Net |
|---|---:|---:|
| GJ_MOMENTUM_LONG | 35 | +$202.73 |
| GJ_PULLBACK_SHORT | 51 | +$256.63 |

## GBPUSD Swing V5

The V4 core is unchanged. The V5 wrapper adds a lower-risk H4 pullback-resumption
setup during 08:00, 12:00 and 16:00 UTC completed bars.

| Window | Net | Trades | PF | Win rate | Max DD |
|---|---:|---:|---:|---:|---:|
| 10 years | +$646.26 | 125 | 2.19 | 67.20% | 1.74% |
| 5 years | +$273.93 | 59 | 2.10 | 67.80% | 1.70% |
| 3 years | +$104.06 | 33 | 1.66 | 60.61% | 1.47% |
| 2 years | +$154.30 | 22 | 2.95 | 72.73% | 0.75% |
| 1 year | +$57.04 | 7 | 4.73 | 85.71% | 0.76% |
| 1 month | +$8.23 | 1 | no losses | 100.00% | 0.33% |

Compared with frozen GBPUSD V4 over ten years:

- trades increased from 93 to 125: +34.4%
- net profit increased from approximately $573.17 to $646.26
- PF changed from approximately 2.37 to 2.19
- max DD changed from approximately 1.48% to 1.74%

This is a moderate frequency increase rather than satellite-style activity.

## One-year engine income

| Engine | Risk | Trades | Income |
|---|---:|---:|---:|
| GBPUSD Satellite V2 | 0.25% | 173 | +$358.54 |
| EURUSD Satellite V7 | 0.25% | 25 | +$156.40 |
| GBPJPY Satellite V7 | 0.25% | 19 | +$129.33 |
| All satellites | matched | 217 | +$644.28 |
| GBPUSD Swing V5 | core 0.35%, add-on 0.20% | 7 | +$57.04 |
| Full system arithmetic total | mixed | 224 | +$701.32 |

The closed-trade arithmetic satellite PF was approximately 1.63 and the full
system PF approximately 1.68. The closed-trade drawdowns were approximately
2.47% and 2.45%, respectively.

## Important portfolio limitation

The one-year totals merge closed trades chronologically. They do not fully model
simultaneous floating P/L, cross-symbol correlation blocking, aggregate open-risk
rejection or a mark-to-market portfolio equity curve. They are not a final live
portfolio result.

## Decision

- GBPUSD V4 core remains frozen
- GBPUSD Swing V5 add-on remains READ_ONLY
- GBPUSD Satellite V2 remains unchanged
- EURUSD V7 remains READ_ONLY
- GBPJPY V7 remains approval/forward-demo only
- keep the branch and PR draft until local tests, a concurrent portfolio backtest,
  event-calendar coverage and at least 30 reconciled forward trades per new
  satellite are completed
