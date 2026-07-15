# V13 10-Year Uploaded-Data Intraday Backtest Report

Status: **Prototype uploaded-data replay — not an official V11 raw candidate-ledger replay**

## Data used

| Symbol | File rows | Start | End | Median spread |
|---|---:|---|---|---:|
| GBPUSD | 782,638 | 2016-01-04 00:00:00 | 2026-07-03 17:45:00 | 0.60 pips |
| EURUSD | 782,535 | 2016-01-04 00:00:00 | 2026-07-03 17:45:00 | 0.40 pips |
| GBPJPY | 782,409 | 2016-01-04 00:00:00 | 2026-07-03 17:45:00 | 1.80 pips |

Test window: **2016-07-03 to 2026-07-03**. Timeframe used: **M5**.

## Intraday-only result

| Metric | Value |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $1,171.77 |
| Net profit | $-3,828.23 |
| Return | -76.56% |
| Candidates | 23,007 |
| Accepted trades | 4,007 |
| Rejected trades | 19,000 |
| Wins | 1,324 |
| Losses | 2,683 |
| Win rate | 33.04% |
| Profit factor | 0.808 |
| Max drawdown | 78.46% |
| Average trade | $-0.96 |
| Max win | $27.33 |
| Max loss | $-18.67 |

## Profit by symbol

| Symbol | Trades | Wins | Win rate | Net profit | Return on $5k | PF | Avg trade | Avg R |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| EURUSD | 955 | 341 | 35.71% | $-151.63 | -3.03% | 0.968 | $-0.16 | -0.004 |
| GBPJPY | 1,255 | 418 | 33.31% | $-1,420.52 | -28.41% | 0.746 | $-1.13 | -0.176 |
| GBPUSD | 1,797 | 565 | 31.44% | $-2,256.08 | -45.12% | 0.765 | $-1.26 | -0.158 |

## V12 comparison

| Scenario | Net profit | Ending balance | Return | Increase vs V12 | Increase % | Note |
|---|---:|---:|---:|---:|---:|---|
| V12 Final max-history only | $3,201.58 | $8,201.58 | 64.03% | $0.00 | 0.00% | Existing V12 max-history research result |
| Uploaded-data V11/V13 intraday only | $-3,828.23 | $1,171.77 | -76.56% |  |  | M5 2016-07-03 to 2026-07-03 prototype intraday replay |
| V12 Final + uploaded-data intraday additive estimate | $-626.65 | $4,373.35 | -12.53% | $-3,828.23 | -119.57% | Capacity-unadjusted additive comparison |

## Result interpretation

This uploaded-data intraday prototype **failed** the 10-year test. It produced a negative net result and would reduce the V12 Final max-history result if added naively.

This does **not** prove the earlier V11 available-data estimate is invalid, because this run does not use the original V11 accepted/rejected candidate ledger. It proves that this reconstructed M5 trend-pullback intraday implementation should not be promoted.

## Required next step

To validate the actual V11/V13 integration, the repo needs the true V11 signal generator or the original V11 candidate ledger, then the same uploaded 10-year M5/M1 data should be replayed through the V13 risk governor.
