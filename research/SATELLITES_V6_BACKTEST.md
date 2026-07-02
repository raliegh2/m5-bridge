# Satellite V6 backtest and validation report

## Implemented structure

- GBPUSD V4 Swing: unchanged
- GBPUSD Satellite V2: unchanged
- EURUSD V6:
  - retained 10:00 quality setup
  - independent London pullback family
  - compression-breakout family
  - defensive New York continuation family
- GBPJPY V6:
  - retained premium breakout-retest
  - expansion-breakout addition
  - compression-expansion family
  - breakout second-leg family
  - restricted 60%-65% ATR tier

All setup families have separate switches. Families that failed development and
validation checks are present but disabled by default.

## Methodology limitation

The retained V5 core results came from the previously documented quality-gate
replay. The new V6 additions were simulated independently from the raw M15 data
with next-bar entries, spread floors, slippage, ATR stops, partial exits,
break-even logic, trailing stops and a swap proxy.

The component totals below combine retained-core trades and independently
simulated V6 additions chronologically, allow one trade per symbol per day, and
give the retained core priority on same-day conflicts. This is not a complete
integrated signal re-simulation. A fresh MT5 end-to-end backtest remains required
before promotion.

The supplied EURUSD and GBPJPY M15 histories begin in June 2022, so no honest
10-year M15 test is available.

## EURUSD V6

### Enabled research components

1. Retained 10:00 quality setup at 0.10% risk
2. Long compression-breakout/retest at 0.075% risk
3. Defensive New York short continuation at 0.075% risk

The independent London pullback family was implemented but disabled because its
validation result was negative.

### New V6 models only

| Window | Net | Trades | Trades/week | PF | Win rate | Closed-trade DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$40.57 | 39 | 0.19 | 1.79 | 64.10% | 0.47% |
| 3 years | +$51.96 | 28 | 0.18 | 2.97 | 75.00% | 0.27% |
| 2 years | +$29.12 | 17 | 0.16 | 2.58 | 70.59% | 0.27% |
| 1 year | +$18.50 | 9 | 0.17 | 3.44 | 77.78% | 0.08% |
| 1 month | $0.00 | 0 | 0.00 | n/a | n/a | 0.00% |

The new-model full-sample result remained positive after a 25% execution-cost
stress: +$36.17, PF 1.69.

### Retained core plus V6 additions

| Window | Net | Trades | Trades/week | PF | Win rate | Closed-trade DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$76.53 | 69 | 0.33 | 1.69 | 65.22% | 0.48% |
| 3 years | +$82.20 | 53 | 0.34 | 2.09 | 71.70% | 0.41% |
| 2 years | +$59.89 | 32 | 0.31 | 2.40 | 71.88% | 0.19% |
| 1 year | +$26.99 | 16 | 0.31 | 2.38 | 75.00% | 0.12% |
| 1 month | $0.00 | 0 | 0.00 | n/a | n/a | 0.00% |

EURUSD improved materially, but its frequency and annual dollar profit remain
well below the GBPUSD satellite. It remains READ_ONLY research.

## GBPJPY V6

### Enabled research components

1. Retained premium breakout-retest at 0.10% risk
2. High-volume 32-bar expansion breakout followed by a retest at 0.10% risk

The explicit contraction filter, second-leg continuation and restricted 60%-65%
ATR tier were implemented but disabled because no robust incremental edge was
found.

### New expansion model only

| Window | Net | Trades | Trades/week | PF | Win rate | Closed-trade DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$42.49 | 9 | 0.05 | no losses | 100.00% | 0.00% |
| 3 years | +$31.23 | 7 | 0.04 | no losses | 100.00% | 0.00% |
| 2 years | +$15.21 | 4 | 0.04 | no losses | 100.00% | 0.00% |
| 1 year | +$10.04 | 3 | 0.06 | no losses | 100.00% | 0.00% |
| 1 month | $0.00 | 0 | 0.00 | n/a | n/a | 0.00% |

The nine-trade sample is too small to treat the no-loss result as a dependable
profit factor. The result remained +$41.70 under 25% execution-cost stress.

Seven of the nine new signals occurred on dates already used by the retained
premium model. Under one-trade-per-symbol-per-day priority, only two genuinely
new trade days were added.

### Retained core plus unique V6 additions

| Window | Net | Trades | Trades/week | PF | Win rate | Closed-trade DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$128.87 | 23 | 0.12 | 6.68 | 82.61% | 0.12% |
| 3 years | +$101.64 | 18 | 0.11 | 6.89 | 83.33% | 0.12% |
| 2 years | +$48.66 | 9 | 0.09 | 5.31 | 77.78% | 0.11% |
| 1 year | +$30.17 | 5 | 0.10 | 6.22 | 80.00% | 0.11% |
| 1 month | $0.00 | 0 | 0.00 | n/a | n/a | 0.00% |

GBPJPY remains an approval-mode/forward-demo candidate. Its apparent edge is
strong, but the sample remains too small for unattended execution.

## Comparison with GBPUSD Satellite V2

The prior GBPUSD Satellite V2 one-year result was approximately +$358.54.
Comparable V6 component totals are:

- EURUSD V6 one year: +$26.99
- GBPJPY V6 one year: +$30.17

The new models improve both symbols, but they do not match GBPUSD dollar profit.
The main limitation remains opportunity count, not position size. Raising risk
to force equal dollar profit would be statistically unsafe.

## Disabled families

- EURUSD independent London pullback: negative validation performance
- GBPJPY explicit compression-contraction filter: PF below 1.0 or negative
  development performance
- GBPJPY breakout second-leg continuation: no parameter neighborhood met the
  minimum development/validation trade and profitability gates
- GBPJPY restricted 60%-65% ATR tier: no robust incremental edge

## Decision

- GBPUSD V4 Swing: unchanged
- GBPUSD Satellite V2: unchanged research/demo status
- EURUSD V6: READ_ONLY research
- GBPJPY V6: approval-mode/forward-demo candidate
- keep the branch and PR draft until a fresh integrated backtest, local tests and
  at least 30 reconciled forward trades per enabled symbol are completed
