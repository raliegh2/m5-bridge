# V14.3 Under-10 DD Target Combined Rework

Status: **research-only event-based replay; no same-trade result lookahead; V12 remains aggregate because no V12 accepted-trade ledger was found**

## Objective

Bring the conservative combined drawdown proxy **under 10%** while keeping the combined two-engine reference result above the **$13,000** target.

## Replay method

The ICT satellite uses the same realistic event-based replay:

1. signals are processed by entry time;
2. risk is assigned before the trade result is known;
3. PnL is applied only at exit time;
4. open trades reserve risk capacity until exit;
5. no current or future trade result is used for entry, sizing, throttling, or skipping.

## Fixed V14.3 signal filters retained

| Filter | Action |
|---|---|
| GBPJPY breakout-fade family | Excluded |
| GBPUSD sweep_reclaim_15 | Excluded |
| Tuesday entries | Excluded |
| 07:00 hour | Excluded |
| 13:00 hour | Excluded |
| Remaining stronger sweep/reclaim/liquidity-fade signals | Accepted if risk allows |

## Selected under-10 profile

| Setting | Value |
|---|---:|
| Active ICT risk | 0.45% |
| Drawdown throttle trigger | 8.00% conservative combined DD proxy |
| Throttle risk | 0.05% |
| Hard DD trigger | 9.70% conservative combined DD proxy |
| Max ICT open-risk cap | 1.25% |

## Result comparison

| Metric | Previous DD-reduced | Selected under-10 |
|---|---:|---:|
| ICT source signals | 4,303 | 4,303 |
| ICT accepted trades | 2,143 | 4,303 |
| ICT skipped trades | 2,160 | 0 |
| ICT ending balance | $10,087.28 | $10,208.81 |
| ICT net result | $5,087.28 | $5,208.81 |
| ICT profit factor | 1.164 | 1.237 |
| ICT realized DD | 5.82% | 4.32% |
| Conservative combined proxy DD | 11.07% | 9.57% |
| Max ICT open risk used | 0.90% | 0.90% |
| Combined reference ending | $13,288.86 | $13,410.39 |
| Combined reference net | $8,288.86 | $8,410.39 |
| Combined reference return | 165.78% | 168.21% |

## Improvement over previous DD-reduced target

| Metric | Change |
|---|---:|
| Conservative combined DD reduction | 1.50 percentage points |
| Combined ending balance change | $121.53 |
| Profit factor change | 0.073 |

## Max-profit under-10 reference

A more aggressive profile also stayed under 10% in this historical replay:

| Metric | Max-profit under-10 reference |
|---|---:|
| Combined reference ending | $14,477.14 |
| Conservative combined proxy DD | 9.98% |
| ICT profit factor | 1.395 |
| Max ICT open risk used | 1.50% |

This reference is not selected because its active risk is much higher. The selected profile is the safer under-10 target version.

## Decision

The selected profile keeps the combined reference ending balance above **$13,000** and brings the conservative combined DD proxy below **10%**:

- Combined reference ending: **$13,410.39**
- Conservative combined DD proxy: **9.57%**
- ICT profit factor: **1.237**

## Limitations

1. V12 remains aggregate-only because the exact V12 accepted-trade ledger is missing.
2. This is historical research, not production proof.
3. V14.3 filters were derived from historical behavior, so forward testing remains required.
4. The combined result is not a true merged chronological replay until the V12 ledger is exported.
