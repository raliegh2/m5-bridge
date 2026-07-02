# Satellite V3 backtest

## Implemented portfolio structure

- GBPUSD V4 Swing: unchanged
- GBPUSD Satellite V2: existing London/M15 research model
- EURUSD Satellite V3: H1 long bias, London M15 pullback and breakout-retest
- GBPJPY Satellite V3: H1 trend/volatility regime, London breakout and M15 retest

The new EURUSD and GBPJPY models were tested with next-bar entries, spread
floors, slippage, ATR stops, partial exits, break-even logic, ATR trailing stops
and swap proxy. Starting balance was $5,000 per independently tested model.

## Data limits

The supplied EURUSD and GBPJPY M15 files begin in June 2022. Therefore, these
models can only be honestly tested over approximately four years, plus 3-year,
2-year, 1-year and 1-month windows. A 10-year M15 result is not available from
the supplied data.

## EURUSD Satellite V3

EURUSD was implemented as long-only initially.

| Window | Net | Return | Trades | Trades/week | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | -$396.01 | -7.92% | 221 | 1.05 | 0.58 | 9.02% |
| 3 years | -$296.04 | -5.92% | 165 | 1.05 | 0.58 | 6.30% |
| 2 years | -$171.34 | -3.43% | 108 | 1.04 | 0.63 | 3.93% |
| 1 year | -$26.93 | -0.54% | 56 | 1.07 | 0.86 | 1.93% |
| 1 month | $0.00 | 0.00% | 0 | 0.00 | n/a | 0.00% |

Decision: **rejected and disabled**. The long-only change improved recent PF
relative to prior EURUSD models, but the strategy still has negative expectancy
and remains far below the three-trades-per-week target.

## GBPJPY Satellite V3

| Window | Net | Return | Trades | Trades/week | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$2.11 | +0.04% | 62 | 0.29 | 1.02 | 0.97% |
| 3 years | -$1.28 | -0.03% | 46 | 0.29 | 0.99 | 0.97% |
| 2 years | +$1.66 | +0.03% | 27 | 0.26 | 1.03 | 0.72% |
| 1 year | +$11.48 | +0.23% | 11 | 0.21 | 1.63 | 0.23% |
| 1 month | $0.00 | 0.00% | 0 | 0.00 | n/a | 0.00% |

Decision: **research-only and disabled**. The most recent year is positive, but
11 trades are not enough evidence. Full-sample PF is only 1.02 and activity is
far below target.

## Portfolio conclusion

Neither new satellite is ready to join the GBPUSD engines.

- Keep GBPUSD V4 unchanged.
- Keep the existing GBPUSD Satellite V2 as research/demo only under its current
  validation status.
- Keep EURUSD Satellite V3 disabled.
- Keep GBPJPY Satellite V3 disabled.
- Do not merge this branch for unattended live trading.

The test shows that adding retest logic and lower-timeframe confirmation reduced
GBPJPY drawdown and improved its recent sample, but did not produce enough trades
or a robust full-sample edge. EURUSD remains structurally unprofitable under the
current long-only continuation framework.
