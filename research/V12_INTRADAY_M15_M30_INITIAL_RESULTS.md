# Initial M15/M30 intraday research result

Status: **FAILED THE PROMOTION GATE — DO NOT ROUTE TO MT5**

Data: `GBPUSD_M5.csv`, 50,000 M5 bars from 2025-10-28 through 2026-07-01.
The first 60% was used for parameter selection and the final 40% was held out
for validation.

## Selected development configuration

- M15 completed-bar entry
- M30 EMA20/EMA50 and ADX trend confirmation
- M15 pullback or 12-bar breakout
- 07:00–17:00 UTC session
- 1.5 ATR stop and 2.0R target
- 0.25% risk per trade
- 1.0-pip spread plus 0.2-pip slippage per side
- Maximum three-hour hold
- One position at a time

## Results

| Segment | Net | Average/week | Trades | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|
| Development 60% | +$48.19 | +$1.66 | 151 | 1.043 | 4.67% |
| Validation 40% | **-$84.23** | **-$5.15** | 112 | **0.903** | 3.53% |
| Full sample reference | -$44.88 | -$0.93 | 267 | 0.978 | 4.67% |

The requested $50/week target was not achieved. Increasing risk would multiply
the loss and drawdown without fixing the negative validation expectancy, so it
is not a valid solution. This experiment remains backtest-only and is not
connected to the final V12 executor.

## Next research requirement

Obtain several years of GBPUSD M5 data, then test genuinely different signal
families with rolling walk-forward validation. No intraday engine should be
promoted unless validation profit factor exceeds 1.10 after costs, drawdown
stays inside the agreed limit, and results remain positive across multiple
market regimes.
