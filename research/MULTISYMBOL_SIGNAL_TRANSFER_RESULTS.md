# Multi-symbol signal-transfer backtest

## Data supplied

- GBPUSD M15/M30: July 2025 through July 2026
- EURUSD M15: June 2022 through July 2026
- EURUSD M30/H1: longer history for indicator warm-up
- GBPJPY M15: June 2022 through July 2026
- GBPJPY M30/H1: longer history for indicator warm-up

All M15 signals were evaluated with H1 and M30 confirmation, next-bar entry,
ATR stops, broker spread fields with minimum spread floors, slippage, one setup
of each type per day, forced intraday exits, and risk-capped sizing.

## Direct transfer of current Satellite V2 rules

| Symbol | Trades | Net | PF | Win rate | Max DD | Average/trade |
|---|---:|---:|---:|---:|---:|---:|
| GBPUSD | 150 | -$42.90 | 0.94 | 28.67% | 2.89% | -$0.29 |
| EURUSD | 665 | -$724.27 | 0.77 | 26.17% | 17.82% | -$1.09 |
| GBPJPY | 560 | -$216.44 | 0.94 | 29.64% | 7.43% | -$0.39 |

The rules did not transfer profitably across all symbols in this independent
implementation. Using every valid signal as an entry produced a combined loss.

## London-session result

| Symbol | London trades | London net | London PF | Average/trade |
|---|---:|---:|---:|---:|
| GBPUSD | 110 | -$99.91 | 0.81 | -$0.91 |
| EURUSD | 510 | -$389.41 | 0.82 | -$0.76 |
| GBPJPY | 426 | -$133.50 | 0.95 | -$0.31 |

The requested London focus did not pass this transfer test. It would be unsafe
to activate these London rules on EURUSD or GBPJPY merely to increase trade
frequency.

## Defensive New York variant

The defensive variant used:

- H1 ADX at least 25
- matching M30 trend
- body ratio at least 0.35
- volume ratio at least 1.0
- RSI at least 57 for longs or no more than 43 for shorts
- 1.35 ATR stop
- 2.5R target
- break-even after 1.25R
- 0.10% risk instead of 0.25%

| Symbol | NY trades | NY net | NY PF | Average/trade |
|---|---:|---:|---:|---:|
| GBPUSD | 15 | +$8.09 | 1.25 | +$0.54 |
| EURUSD | 72 | -$148.09 | 0.35 | -$2.06 |
| GBPJPY | 39 | +$63.08 | 1.77 | +$1.62 |

Only GBPJPY's defensive New York subset passed a basic profitability screen.
EURUSD failed badly. GBPUSD was positive but too weak and too small a sample to
promote independently.

## Recent July 2025-July 2026 slice

| Symbol | All improved trades | Net | Defensive NY net |
|---|---:|---:|---:|
| GBPUSD | 125 | -$89.96 | +$8.09 |
| EURUSD | 144 | -$22.80 | -$40.21 |
| GBPJPY | 105 | +$24.99 | -$9.40 |

GBPJPY's longer defensive-New-York edge did not remain positive in the most
recent one-year slice. This is evidence of regime instability.

## Decision

No additional symbol is enabled for live or approval-mode execution from these
results.

- EURUSD: rejected for both London and defensive New York.
- GBPJPY London: rejected.
- GBPJPY defensive New York: research-only because recent performance was
  negative despite a positive full available sample.
- GBPUSD remains governed by its existing V4 and Satellite V2 validation; this
  independent transfer test must not overwrite the previously frozen results.

The correct next step is rolling walk-forward testing with symbol-specific
parameters, not applying one GBPUSD parameter set to every instrument. Any
future multi-symbol controller must use a symbol/setup allowlist and retain the
0.50% portfolio open-risk and daily-new-risk caps.
