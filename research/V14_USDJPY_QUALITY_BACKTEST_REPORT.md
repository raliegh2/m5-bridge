# V14 USDJPY Quality Enhancement — Whole-System Backtest

The synchronized candidate trades GBPUSD, EURUSD, GBPJPY, AUDUSD and USDJPY under the existing five-position and 1.50% total open-risk limits.

## Frozen USDJPY improvement

The D1/H4 40-bar breakout remains unchanged. Two completed-candle quality conditions were added:

- H4 body ratio must be at least 0.30 rather than 0.20.
- Signal-end hour must be 08:00, 12:00, 16:00 or 20:00 UTC; 00:00 and 04:00 UTC signals are skipped.

Risk remains 0.25%, with the existing 1.5 ATR stop, 3R target, 2 ATR trail and 30 H4-bar maximum hold.

| Segment | Original trades | Original net R | Original PF | Improved trades | Improved net R | Improved PF |
|---|---:|---:|---:|---:|---:|---:|
| Development | 354 | 39.35R | 1.234 | 239 | **43.89R** | **1.401** |
| Final validation | 186 | 22.79R | 1.252 | 141 | **25.62R** | **1.407** |

## Whole-system results from a $5,000 starting balance

Gross income is the sum of all winning-trade P/L before losing trades are deducted.

| Requested period | Actual tested dates | Gross income | Gross loss | Net profit | Return | Avg monthly net | Trades | PF | Max DD | Stress DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 years* | 2012-11-26 to 2022-03-04 | **$8,927.21** | $6,881.36 | **$2,045.85** | 40.92% | $18.40 | 1,045 | 1.297 | 5.28% | 5.65% |
| 5 years | 2017-03-04 to 2022-03-04 | **$3,930.19** | $3,282.13 | **$648.06** | 12.96% | $10.80 | 571 | 1.197 | 5.23% | 5.65% |
| 3 years | 2019-03-04 to 2022-03-04 | **$2,412.25** | $1,939.09 | **$473.16** | 9.46% | $13.14 | 349 | 1.244 | 3.86% | 4.78% |
| 2 years | 2020-03-04 to 2022-03-04 | **$1,603.96** | $1,378.30 | **$225.65** | 4.51% | $9.41 | 245 | 1.164 | 3.86% | 4.78% |
| 1 year | 2021-03-04 to 2022-03-04 | **$753.94** | $651.99 | **$101.95** | 2.04% | $8.50 | 115 | 1.156 | 3.86% | 4.78% |

*The common source history contains approximately 9.27 years, not a complete ten years, and ends in March 2022.

## Improvement versus V13

| Period | Previous net | Improved net | Increase | Previous DD | Improved DD |
|---|---:|---:|---:|---:|---:|
| Maximum history | $1,884.17 | **$2,045.85** | **+$161.68** | 5.76% | **5.28%** |
| 5 years | $522.27 | **$648.06** | **+$125.79** | 5.46% | **5.23%** |
| 3 years | $394.29 | **$473.16** | **+$78.87** | 4.10% | **3.86%** |
| 2 years | $160.63 | **$225.65** | **+$65.02** | 4.10% | **3.86%** |
| 1 year | $51.15 | **$101.95** | **+$50.80** | 4.10% | **3.86%** |

The candidate remains READ_ONLY. This is an OHLC/candidate-ledger replay rather than a tick-level broker simulation.