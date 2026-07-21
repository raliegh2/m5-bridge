# GBPUSD Breakout V2 profit optimization

## Correct baseline

The source is the local 16,331-bar GBPUSD H4 export covering 2016-01-04
through 2026-07-01. The higher-cost model uses a 1.5-pip spread floor,
0.6-pip slippage per side, and the existing swap proxy.

| Metric | Original V2 | Quality-tier V2 | Change |
|---|---:|---:|---:|
| Net profit | $12,791.79 | **$16,621.64** | **+$3,829.85 (+29.9%)** |
| Ending balance | $112,791.79 | **$116,621.64** | +$3,829.85 |
| Trades | 160 | 160 | unchanged |
| Profit factor | 1.2914 | **1.3134** | +0.0220 |
| Maximum drawdown | 5.66% | 6.56% | +0.90 pp |

## Chronological holdout

The sizing rule was selected using 2016-2021 development folds. The later
2022-2026 period was then evaluated separately.

| Metric | Original V2 | Quality-tier V2 | Change |
|---|---:|---:|---:|
| Net profit | $6,805.43 | **$7,450.49** | **+$645.06 (+9.5%)** |
| Profit factor | **1.4024** | 1.3678 | -0.0346 |
| Maximum drawdown | 3.21% | 3.99% | +0.78 pp |

## Retained rule

Entries, stops, targets, trailing, and the configured base risk remain
unchanged. Completed breakout candles with both volume ratio at least 1.0 and
range at least 1.0 ATR receive 1.25 times base risk. Other valid setups receive
0.75 times base risk. With the documented 0.50% base risk, actual risk is
0.625% or 0.375%, both below the existing 1% engine cap.

Exit-management and hour-allocation candidates were rejected. The original
exit parameters beat the development exit grid. Hour reallocation raised the
full-period result but failed badly in the later holdout.

This remains historical research, not a profit guarantee. Demo forward
reconciliation is required before automatic trading.
