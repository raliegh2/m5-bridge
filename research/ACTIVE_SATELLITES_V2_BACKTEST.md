# Active Satellites V2 backtest

## Scope

The new system was tested over rolling lookback windows ending 2026-07-02:

- 10 years
- 5 years
- 3 years
- 2 years
- 1 month

GBPUSD used the frozen V4 swing engine. EURUSD and GBPJPY used the new active
H4 satellite rules. Each model started from a fresh $5,000 account for its
individual result.

Execution assumptions included next-bar entry, spread floors, slippage, swap
proxy, ATR stops, partial exits, break-even logic and ATR trailing stops.

## GBPUSD V4

| Window | Net | Return | Trades | PF | Max DD |
|---|---:|---:|---:|---:|---:|
| 10 years | +$573.17 | +11.46% | 93 | 2.37 | 1.48% |
| 5 years | +$229.05 | +4.58% | 44 | 2.18 | 1.52% |
| 3 years | +$78.29 | +1.57% | 22 | 1.61 | 1.10% |
| 2 years | +$114.22 | +2.28% | 15 | 2.64 | 0.89% |
| 1 month | +$8.23 | +0.16% | 1 | infinite | 0.33% |

## EURUSD Active Satellite V2

| Window | Net | Return | Trades | Trades/week | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| 10 years | -$415.00 | -8.30% | 349 | 0.67 | 0.77 | 8.37% |
| 5 years | -$273.51 | -5.47% | 189 | 0.72 | 0.74 | 5.74% |
| 3 years | -$150.09 | -3.00% | 96 | 0.61 | 0.74 | 4.16% |
| 2 years | -$130.27 | -2.61% | 69 | 0.66 | 0.69 | 4.27% |
| 1 month | -$29.78 | -0.60% | 3 | 0.70 | 0.00 | 0.68% |

EURUSD failed both the profitability gate and the three-trades-per-week target.

## GBPJPY Active Satellite V2

| Window | Net | Return | Trades | Trades/week | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| 10 years | -$45.91 | -0.92% | 55 | 0.11 | 0.80 | 1.92% |
| 5 years | -$66.71 | -1.33% | 37 | 0.14 | 0.60 | 1.92% |
| 3 years | -$49.16 | -0.98% | 17 | 0.11 | 0.48 | 1.43% |
| 2 years | -$45.03 | -0.90% | 11 | 0.11 | 0.36 | 1.01% |
| 1 month | $0.00 | 0.00% | 0 | 0.00 | n/a | 0.00% |

GBPJPY was far below the intended activity target and did not produce a positive
expectancy.

## Approximate three-engine combination

The combined figures merge realized trade P/L chronologically on one $5,000
starting balance. They are an approximation and do not fully simulate simultaneous
open-equity correlation or portfolio entry blocking.

| Window | Net | Return | Trades | Combined PF | Realized-sequence DD |
|---|---:|---:|---:|---:|---:|
| 10 years | +$112.26 | +2.25% | 497 | 1.04 | 3.82% |
| 5 years | -$111.16 | -2.22% | 270 | 0.92 | 3.79% |
| 3 years | -$120.96 | -2.42% | 135 | 0.85 | 3.70% |
| 2 years | -$61.08 | -1.22% | 95 | 0.89 | 3.79% |
| 1 month | -$21.55 | -0.43% | 4 | 0.28 | 0.39% |

## Decision

- GBPUSD V4 remains the only profitable and robust engine in this configuration.
- EURUSD Active Satellite V2 remains disabled.
- GBPJPY Active Satellite V2 remains disabled.
- PR #7 must remain draft and unmerged.

The satellites did not achieve the requested three trades per week and reduced
portfolio profitability over the 5-year, 3-year, 2-year and 1-month windows.
The next iteration should redesign the satellite entry logic rather than loosen
filters on these losing models.
