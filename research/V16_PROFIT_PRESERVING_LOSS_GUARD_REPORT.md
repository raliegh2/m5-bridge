# V16 Profit-Preserving Loss Guard

V16 preserves full strategy risk for GBPUSD precision, AUDUSD, improved USDJPY and EURUSD. The V15 24-trade adaptive loss guard is applied only to GBPJPY, the weakest recent component.

## Backtest summary from a $5,000 starting balance

| Period | Gross income | Gross loss | Net profit | Avg monthly net | PF | Max DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Maximum common history | $7,589.06 | $5,646.52 | $1,942.54 | $17.47 | 1.344 | 4.53% | 5.24% |
| 5 years | $3,577.34 | $2,733.86 | $843.47 | $14.06 | 1.309 | 4.53% | 4.96% |
| 3 years | $2,202.03 | $1,550.33 | $651.70 | $18.10 | 1.420 | 2.60% | 3.53% |
| 2 years | $1,457.62 | $1,037.54 | $420.08 | $17.52 | 1.405 | 2.60% | 3.53% |
| 1 year | $725.18 | $565.29 | $159.90 | $13.33 | 1.283 | 2.60% | 3.53% |

## Comparison with V14

| Period | V14 net | V16 net | Net change | V14 gross loss | V16 gross loss | Loss reduction | V14 DD | V16 DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Maximum common history | $2,045.85 | $1,942.54 | -$103.31 | $6,881.36 | $5,646.52 | $1,234.84 | 5.28% | 4.53% |
| 5 years | $648.06 | $843.47 | +$195.41 | $3,282.13 | $2,733.86 | $548.26 | 5.23% | 4.53% |
| 3 years | $473.16 | $651.70 | +$178.54 | $1,939.09 | $1,550.33 | $388.76 | 3.86% | 2.60% |
| 2 years | $225.65 | $420.08 | +$194.42 | $1,378.30 | $1,037.54 | $340.76 | 3.86% | 2.60% |
| 1 year | $101.95 | $159.90 | +$57.95 | $651.99 | $565.29 | $86.70 | 3.86% | 2.60% |

The maximum common source period is approximately 9.27 years ending March 2022, not a complete current-through-2026 ten-year sample. This is an OHLC/candidate-ledger replay rather than tick-level broker execution.
