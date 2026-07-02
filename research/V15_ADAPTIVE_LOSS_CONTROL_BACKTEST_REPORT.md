# V15 Adaptive Loss-Control Backtest

V15 keeps GBPUSD precision protected and applies a 24-trade adaptive guard to AUDUSD, USDJPY, EURUSD and GBPJPY. The minimum history is 20 closed accepted trades. Full risk requires PF >= 1.15 and net R > 0. Half risk requires PF >= 0.95 and net R > -2R. A failed engine pauses for 60 days, then receives one half-risk probe.

| Period | Gross income | Gross loss | Loss reduction vs V14 | Net profit | Net change vs V14 | PF | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Maximum common history | $6,092.68 | $4,507.86 | $2,373.50 (34.49%) | $1,584.82 | -$461.02 | 1.352 | 3.51% | 4.31% |
| 5 years | $2,731.56 | $2,070.66 | $1,211.47 (36.91%) | $660.90 | +$12.84 | 1.319 | 3.34% | 3.68% |
| 3 years | $1,631.20 | $1,220.23 | $718.86 (37.07%) | $410.97 | -$62.19 | 1.337 | 2.38% | 2.82% |
| 2 years | $1,073.77 | $788.60 | $589.70 (42.78%) | $285.17 | +$59.51 | 1.362 | 2.38% | 2.82% |
| 1 year | $402.72 | $342.69 | $309.31 (47.44%) | $60.04 | -$41.91 | 1.175 | 1.91% | 2.11% |

Shorter windows reset balance to $5,000 but seed the guard using only earlier closed accepted trades. Open trades at the boundary are not used, avoiding future-outcome leakage.

The maximum-history row covers 26 November 2012 to 4 March 2022, approximately 9.27 years, not a full current-through-2026 ten years. The replay is OHLC/candidate-ledger research, not tick-level broker execution.
