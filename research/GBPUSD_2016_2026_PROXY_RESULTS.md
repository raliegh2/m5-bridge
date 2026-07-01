# GBPUSD Regime-Adaptive Swing Proxy Results

## Dataset

- H4 source: `GBPUSD_H4_201601040000_202607011200.csv`
- Coverage: 2016-01-04 through 2026-07-01
- H4 bars: 16,331
- D1 and W1 bars were derived from the H4 source using completed UTC bars.
- The uploaded file labelled Daily contained repeated H4 rows without a time column, so it was not used as an independent Daily source.

## Execution assumptions

- Completed H4/D1/W1 candles only
- Next-H4-open entry
- One GBPUSD position maximum
- Risk per trade: 0.50%
- Spread: actual bar spread from MT5, with a minimum 0.8 pip assumption
- Slippage: 0.3 pip each exit/entry side
- Partial exit: 50% at 1R
- Remaining position moved to break-even after partial
- ATR trailing stop
- Conservative stop-first same-bar handling
- Time exit near 15 trading days
- No historical news filter because no historical event dataset was supplied

## Fixed-parameter variant results

| Variant | Trades | Net P&L | Profit Factor | Win Rate | Max Drawdown | Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| Trend pullback | 101 | -$7,176.39 | 0.72 | 46.53% | 8.78% | -0.36 |
| Breakout continuation | 99 | -$2,164.87 | 0.89 | 42.42% | 5.71% | -0.13 |
| Range mean reversion | 214 | -$3,770.28 | 0.93 | 48.60% | 9.13% | -0.11 |

## Combined engine

The combined engine opened 151 trades before the permanent 10% drawdown circuit breaker disabled new entries in 2020.

- Net P&L: -$8,170.44
- Ending balance: $91,829.56
- Profit factor: 0.78
- Win rate: 44.37%
- Maximum mark-to-market drawdown: 10.23%
- Sharpe: -0.33

### Combined engine by strategy

| Strategy | Trades | Net P&L | Win Rate |
|---|---:|---:|---:|
| Pullback | 36 | -$3,199.47 | 44.44% |
| Breakout | 36 | -$2,083.74 | 36.11% |
| Mean reversion | 79 | -$2,887.23 | 48.10% |

## Conclusion

The recommended regime-adaptive design did not produce positive expectancy on this GBPUSD dataset with the fixed parameters tested. The breakout variant was the least damaging, but its profit factor remained below 1.0. The combined engine correctly triggered its 10% drawdown circuit breaker, which prevented further losses but also stopped the full-period combined test after 2020.

These results do not justify live deployment. The next research step should isolate the breakout engine, test broad parameter neighborhoods with walk-forward validation, and add a genuine historical GBP/USD news calendar before enabling event-aware logic. Any optimization must reserve untouched out-of-sample periods and must not reuse the full 2016-2026 period for both tuning and final validation.
