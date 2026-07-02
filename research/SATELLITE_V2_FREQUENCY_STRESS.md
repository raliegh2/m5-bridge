# Satellite V2 frequency-stress experiments

## Purpose

The target was to raise average activity from 3.34 trades per week toward at
least five trades per week without accepting low-quality setups.

These experiments used the same July 2025-July 2026 M15 data, execution costs,
0.08-lot requested base size, 0.25% per-trade risk cap, and 0.50% daily new-risk
cap as Satellite V2.

## Rejected additions

| Added setup | Total trades/week | Combined net | Combined PF | Average/trade | Max DD | Added setup contribution |
|---|---:|---:|---:|---:|---:|---:|
| London opening-range breakout | 4.77 | +$120.05 | 1.09 | $0.49 | 4.26% | -$240.62 across 85 trades |
| New York opening-range breakout | 5.14 | -$12.10 | 0.99 | -$0.05 | 3.68% | -$297.03 across 106 trades |
| Second London pullback/re-entry | 3.78 | +$173.83 | 1.17 | $0.89 | 2.87% | -$142.78 across 29 trades |

## Decision

The New York opening-range variant reached more than five trades per week, but
it destroyed the edge. The London opening-range and London re-entry variants
also materially reduced profit factor and average profit per trade.

They were not added to the live engine.

An exact five-trade weekly quota is incompatible with a quality-only rule on one
instrument because some weeks do not produce five valid GBPUSD setups. The next
responsible frequency expansion is an independently validated second instrument
or another genuinely independent setup with positive later-period and
walk-forward results. Trade frequency must be an observed outcome, not a forced
entry quota.
