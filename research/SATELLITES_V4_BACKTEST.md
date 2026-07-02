# Satellite V4 backtest

## Implemented changes

### EURUSD V4

- long-only
- 10:00-12:00 UTC only
- H1 EMA20/EMA50 trend, close above EMA20, ADX and positive EMA slope
- matching M30 trend
- M30 ATR-percentile regime
- pullback-resumption only; breakout setup removed
- stronger M15 body, volume and RSI confirmation
- 0.15% risk
- 1.15 ATR stop
- 50% partial at 1R
- 2.2R final target
- 1.5 ATR trail
- maximum 32 M15 bars

### GBPJPY V4

- long-only
- 07:00-12:30 UTC only
- H1 EMA20/EMA50 trend, ADX, ATR percentile and EMA slope
- mandatory M30 eight-bar breakout
- mandatory M15 retest and close back above breakout level
- stronger volume and body confirmation
- 0.10% risk
- 1.40 ATR stop
- 40% partial at 1.2R
- 2.8R final target
- 2 ATR trail
- maximum 64 M15 bars

## Test assumptions

- starting balance: $5,000 per model
- next-bar entry
- conservative stop-first same-bar handling
- spread floor and slippage
- swap proxy
- one position and one new entry per symbol per day
- risk-based sizing

The M15 histories begin in June 2022, so the longest honest test is the full
available period rather than ten years.

## EURUSD V4 result

| Window | Net | Return | Trades | Trades/week | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | -$27.06 | -0.54% | 85 | 0.44 | 0.92 | 1.33% |
| 3 years | -$2.50 | -0.05% | 64 | 0.43 | 0.99 | 1.04% |
| 2 years | +$2.75 | +0.06% | 42 | 0.44 | 1.02 | 0.99% |
| 1 year | -$10.10 | -0.20% | 22 | 0.53 | 0.87 | 0.99% |
| 1 month | $0.00 | 0.00% | 0 | 0.00 | n/a | 0.00% |

The stricter late-London rules materially improved EURUSD compared with V3:
losses and drawdown fell sharply, and the two-year sample became marginally
positive. However, the full sample and most recent year remain below PF 1.0.

**Decision: EURUSD V4 remains disabled.** It does not match the profitability of
the GBPUSD satellite and cannot be promoted by increasing lot size.

## GBPJPY V4 result

| Window | Net | Return | Trades | Trades/week | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$110.31 | +2.21% | 36 | 0.19 | 2.66 | 0.22% |
| 3 years | +$85.38 | +1.71% | 25 | 0.19 | 2.91 | 0.22% |
| 2 years | +$57.02 | +1.14% | 13 | 0.16 | 3.55 | 0.22% |
| 1 year | +$45.73 | +0.91% | 6 | 0.24 | 8.91 | 0.11% |
| 1 month | $0.00 | 0.00% | 0 | 0.00 | n/a | 0.00% |

GBPJPY V4 produced a strong PF and very low drawdown, including positive
training and later-period results. The limitation is frequency and sample size:
only 36 trades over roughly four years and six trades during the latest year.

**Decision: GBPJPY V4 is a promising research/demo candidate, not yet ready for
unattended live execution.** The observed profit is below the GBPUSD satellite's
+$358.54 one-year result because GBPJPY V4 trades far less frequently and risks
only 0.10% per trade.

## Profitability comparison

| Engine | Comparable result | Status |
|---|---:|---|
| GBPUSD Satellite V2 | approximately +$358.54 over available one-year test | research/demo |
| EURUSD V4 | -$10.10 over latest year | disabled |
| GBPJPY V4 | +$45.73 over latest year | research/demo candidate |

Matching the GBPUSD satellite's dollar profit would require either much higher
risk or many more entries. Increasing GBPJPY V4 from 0.10% to roughly 0.78% risk
would scale the latest-year backtest near the GBPUSD satellite result, but it
would violate the current 0.50% maximum-risk rule and would be statistically
unsafe with only six trades. It is not recommended.

## Final decision

- GBPUSD V4 Swing: unchanged
- GBPUSD Satellite V2: unchanged research/demo status
- EURUSD V4: disabled
- GBPJPY V4: READ_ONLY or approval-mode research only
- keep the branch unmerged until GBPJPY records at least 30-50 genuine forward
  trades and EURUSD produces PF above 1.20 out of sample
