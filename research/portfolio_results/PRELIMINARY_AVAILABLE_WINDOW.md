# Preliminary V4 + Satellite backtest

## Data coverage

The available M30 file covers only:

- 2025-07-01 15:00 UTC through 2026-07-01 15:00 UTC

Therefore the requested 10-year, 5-year, 3-year, and 2-year satellite/combined
windows cannot be honestly completed from the supplied data. The backtest runner
returns `INSUFFICIENT_M30_HISTORY` for each of those windows instead of silently
using incomplete data.

## Available one-year development check

Starting balance: **$5,000**

| Component | Trades | Ending balance | Net | Return | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| Frozen V4 Swing | 6 | $5,052.04 | +$52.04 | 1.04% | 4.41 | 0.76% |
| Satellite Intraday | 103 | $5,052.26 | +$52.26 | 1.05% | 1.20 | 1.10% |
| Combined approximation | 109 | $5,104.85 | +$104.85 | 2.10% | 1.37 | 0.97% |

The combined calculation is a normalized-return portfolio approximation. It
multiplies the independently generated V4 and Satellite equity indices. The live
portfolio is more restrictive because it blocks opposing simultaneous exposure.
No opposing overlap candidates occurred in this available one-year sample.

## Interpretation

The satellite materially increased activity from 6 to 109 total trades, but its
one-year PF of approximately 1.20 is below the pre-registered 1.30 minimum for a
production satellite. The combined PF also fell below V4's standalone PF.

This means the satellite is implemented as a **research/demo candidate**, not a
validated production replacement. Do not merge it to main or enable unattended
live execution until the complete 10/5/3/2-year tests and forward validation are
completed.
