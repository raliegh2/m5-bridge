# V9 Uploaded-Data Backtest Report

## Verdict

The uploaded files support an exact ten-year rerun of the **GBPUSD Swing V5** component, but they do **not** support a complete ten-year Strategy Engine V9 portfolio backtest.

The full V9 test remains blocked because:

- no GBPUSD M15 file was supplied, so `GBPUSD_SATELLITE_V3` cannot be regenerated;
- EURUSD M15 begins on 2022-06-22;
- GBPJPY M15 begins on 2022-06-06;
- the file labelled GBPUSD Daily contains six rows per date and no time column, so it is not a valid D1 export.

For the swing rerun, daily trend data was derived from the valid GBPUSD H4 history, matching the existing backtester methodology.

## Exact GBPUSD Swing V5 result

| Metric | Result |
|---|---:|
| Period | 2016-07-01 to 2026-07-01 |
| Starting balance | $5,000.00 |
| Ending balance | $5,646.26 |
| Net profit | **$646.26** |
| Return | **12.93%** |
| Trades | 125 |
| Win rate | 67.20% |
| Profit factor | 2.1925 |
| Maximum drawdown | 1.7403% |

## V4 versus V5

| Metric | V4 | V5 | Change |
|---|---:|---:|---:|
| Net profit | $573.17 | $646.26 | +$73.09 |
| Trades | 93 | 125 | +32 |
| Profit factor | 2.3652 | 2.1925 | -0.1727 |
| Maximum drawdown | 1.4827% | 1.7403% | +0.2576 pp |

V5 increased net profit by **$73.09** and increased trade count by **34.4%**. Its profit factor was slightly lower and drawdown was slightly higher, but both remained strong.

## Window results

| Window | Net profit | Trades | Profit factor | Win rate | Max DD |
|---|---:|---:|---:|---:|---:|
| 10 years | $646.26 | 125 | 2.19 | 67.20% | 1.74% |
| 5 years | $273.93 | 59 | 2.10 | 67.80% | 1.70% |
| 3 years | $104.06 | 33 | 1.66 | 60.61% | 1.47% |
| 2 years | $154.30 | 22 | 2.95 | 72.73% | 0.75% |
| 1 year | $57.04 | 7 | 4.73 | 85.71% | 0.76% |
| 1 month | $8.23 | 1 | No losing trade | 100.00% | 0.33% |

## Ten-year setup contribution

| Setup | Trades | Net profit | Win rate | Profit factor |
|---|---:|---:|---:|---:|
| GBPUSD Swing V5 Pullback Add-on | 35 | $66.75 | 60.00% | 1.5757 |
| Primary 16 UTC Breakout | 53 | $383.19 | 71.70% | 2.6552 |
| Secondary 12 UTC Breakout | 37 | $196.32 | 67.57% | 2.0094 |

## Data status

The strict V9 preflight returned `BLOCKED_INSUFFICIENT_DATA`. This is the correct behavior: a partial dataset must not be presented as a ten-year multi-engine result.

## Files still required for the full V9 backtest

1. GBPUSD M15 covering approximately July 2016 through July 2026.
2. A valid GBPUSD D1 export, or use H4-derived D1 throughout the test.
3. Ten-year EURUSD M15 history.
4. Ten-year GBPJPY M15 history.

Until those inputs exist, the exact ten-year result applies only to the GBPUSD Swing V5 component.
