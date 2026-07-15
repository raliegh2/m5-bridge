# V14.3 DD-Reduced Target Combined Rework

Status: **research-only event-based replay; no same-trade result lookahead; V12 remains aggregate because no V12 accepted-trade ledger was found**

## Objective

Decrease drawdown while keeping the combined two-engine reference result near or above the **$13,000** target.

The previous target build reached the goal, but its conservative combined drawdown proxy was about **12.02%**. This run searches for a lower-drawdown profile that still keeps the combined reference ending balance above **$13,000**.

## Realistic replay rules

The ICT satellite was replayed with the event-based engine:

1. signals are processed by entry time;
2. risk is assigned before the trade result is known;
3. the trade result is applied only at exit time;
4. active trades reserve open-risk capacity until exit;
5. no current or future trade result is used to decide entry, sizing, throttling, or skipping.

## Fixed V14.3 signal filters retained

| Filter | Action |
|---|---|
| GBPJPY breakout-fade family | Excluded |
| GBPUSD sweep_reclaim_15 | Excluded |
| Tuesday entries | Excluded |
| 07:00 hour | Excluded |
| 13:00 hour | Excluded |
| Remaining stronger sweep/reclaim/liquidity-fade signals | Accepted if risk allows |

## Selected lower-drawdown target profile

| Metric | Previous target | DD-reduced target |
|---|---:|---:|
| Profile label | previous_target_r0.35_no_micro_hard12_cap1.25 | dd_throttle_r0.45_t9.5_tr0.25_hard11.0 |
| ICT source signals | 4,303 | 4,303 |
| ICT accepted trades | 3,016 | 2,143 |
| ICT skipped trades | 1,287 | 2,160 |
| ICT ending balance | $10,298.51 | $10,087.28 |
| ICT net result | $5,298.51 | $5,087.28 |
| ICT profit factor | 1.134 | 1.164 |
| ICT realized DD | 6.77% | 5.82% |
| Conservative combined proxy DD | 12.02% | 11.07% |
| Max ICT open risk used | 0.70% | 0.90% |
| Combined reference ending | $13,500.09 | $13,288.86 |
| Combined reference net | $8,500.09 | $8,288.86 |
| Combined reference return | 170.00% | 165.78% |

## Improvement

| Metric | Change |
|---|---:|
| Conservative combined DD reduction | 0.95 percentage points |
| Combined ending balance change | $-211.23 |

## Decision

The DD-reduced target profile keeps the combined reference result above **$13,000** while reducing the conservative combined drawdown proxy from **12.02%** to **11.07%**.

This is an improvement over the previous target build, but it is still **not prop-safe** if the account has a hard 10% total loss rule. It is a higher-drawdown research candidate that reaches the profit target with less drawdown than the prior target build.

## Limitations

1. V12 is still aggregate-only because the exact V12 accepted-trade ledger is missing.
2. This is historical research, not production proof.
3. V14.3 filters were derived from historical behavior, so forward testing remains required.
4. The combined result is not a true merged chronological replay until the V12 ledger is exported.
