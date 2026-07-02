# Satellite V6 matched-risk retest

## Risk policy

- GBPUSD Satellite V2: 0.25% per trade, unchanged
- EURUSD V6: increased to 0.25% per enabled setup
- GBPJPY V6: increased to 0.25% per enabled setup
- GBPUSD V4 Swing: remains unchanged at 0.35% per trade

The satellites now use the same nominal risk percentage as GBPUSD Satellite V2.
The frozen GBPUSD V4 Swing engine was not modified.

## Methodology

- EURUSD and GBPJPY retained-core trades were recalculated from their original
  risk to 0.25%.
- The new V6 EURUSD and GBPJPY models were rerun at 0.25% risk.
- One trade per symbol per day was retained, with the validated core setup taking
  priority over an optional setup on the same date.
- GBPUSD Satellite V2 uses its native one-year result at 0.25% risk.
- GBPUSD V4 Swing uses its native one-year result at 0.35% risk.

The combined portfolio figures are a chronological closed-trade calculation.
They do not model simultaneous floating P/L, correlation blocking, or aggregate
open-risk rejection. Therefore they are not a final portfolio MTM backtest.

## Matched-risk satellite results

### EURUSD V6 at 0.25%

| Window | Net | Return | Trades | PF | Win rate | Closed-trade DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$227.87 | +4.56% | 69 | 1.67 | 65.22% | 1.60% |
| 3 years | +$258.98 | +5.18% | 53 | 2.18 | 71.70% | 1.11% |
| 2 years | +$183.41 | +3.67% | 32 | 2.41 | 71.88% | 0.57% |
| 1 year | +$91.27 | +1.83% | 16 | 2.58 | 75.00% | 0.29% |
| 1 month | $0.00 | 0.00% | 0 | n/a | n/a | 0.00% |

### GBPJPY V6 at 0.25%

| Window | Net | Return | Trades | PF | Win rate | Closed-trade DD |
|---|---:|---:|---:|---:|---:|---:|
| Full available | +$323.00 | +6.46% | 23 | 6.69 | 82.61% | 0.29% |
| 3 years | +$254.93 | +5.10% | 18 | 6.91 | 83.33% | 0.29% |
| 2 years | +$122.48 | +2.45% | 9 | 5.33 | 77.78% | 0.28% |
| 1 year | +$76.25 | +1.53% | 5 | 6.28 | 80.00% | 0.28% |
| 1 month | $0.00 | 0.00% | 0 | n/a | n/a | 0.00% |

## One-year income by engine

| Engine | Risk/trade | Trades | Income | Return |
|---|---:|---:|---:|---:|
| GBPUSD Satellite V2 | 0.25% | 173 | +$358.54 | +7.17% |
| EURUSD V6 | 0.25% | 16 | +$91.27 | +1.83% |
| GBPJPY V6 | 0.25% | 5 | +$76.25 | +1.53% |
| **All satellites** | matched | **194** | **+$526.06** | **+10.52%** |
| GBPUSD V4 Swing | 0.35% | 6 | +$52.04 | +1.04% |
| **Full system** | mixed as above | **200** | **+$578.11** | **+11.56%** |

The closed-trade one-year satellite PF was 1.59, with an approximate closed-trade
DD of 1.50%. Adding GBPUSD V4 Swing increased the PF to 1.63 and total income to
+$578.11, with approximate closed-trade DD of 1.48%.

## GBPUSD V4 Swing trades

| Entry time UTC | Side | Variant | P/L | R | Exit reason |
|---|---|---|---:|---:|---|
| 2025-09-15 20:00 | Long | PRIMARY_16UTC_BREAKOUT | +$6.73 | +0.48R | STOP_OR_TRAIL |
| 2025-10-09 16:00 | Short | SECONDARY_12UTC_BREAKOUT | +$8.83 | +0.56R | STOP_OR_TRAIL |
| 2025-10-28 20:00 | Short | PRIMARY_16UTC_BREAKOUT | +$31.04 | +1.98R | TARGET |
| 2026-01-27 20:00 | Long | PRIMARY_16UTC_BREAKOUT | +$12.50 | +0.73R | STOP_OR_TRAIL |
| 2026-05-01 20:00 | Long | PRIMARY_16UTC_BREAKOUT | -$15.28 | -1.02R | STOP_OR_TRAIL |
| 2026-06-18 16:00 | Short | SECONDARY_12UTC_BREAKOUT | +$8.23 | +0.48R | STOP_OR_TRAIL |

## Decision

The matched-risk profile materially increases EURUSD and GBPJPY dollar income,
but does not make them equal to GBPUSD Satellite V2 because their trade counts
remain much lower.

- GBPUSD Satellite V2 remains the dominant income engine.
- EURUSD V6 remains READ_ONLY research despite the improved matched-risk result.
- GBPJPY V6 remains approval-mode/forward-demo because its PF is based on only
  five one-year trades and 23 full-period trades.
- GBPUSD V4 Swing remains unchanged.
- Do not merge for unattended live execution until a full concurrent portfolio
  backtest and forward-demo reconciliation are completed.
