# V14 Combined V12 Final + ICT Low-DD High-Activity Backtest Estimate

Status: **combined research estimate; exact chronological merged replay limited by missing V12 trade ledger**

## What was combined

This combines the existing V12 Final protected engine with the new ICT high-activity, lower-drawdown profile.

| Component | Net result | Ending balance if standalone | Return | Trades | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| V12 Final protected engine | $3,201.58 | $8,201.58 | 64.03% | 918 | 1.606 | 4.93% |
| ICT low_dd_active_gap60 | $5,819.14 | $10,819.14 | 116.38% | 11,649 | 1.114 | 7.94% |

## Combined result if placed together

| Metric | Combined estimate |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $14,020.72 |
| Net result | $9,020.72 |
| Return | 180.41% |
| Combined trades | 12,567 |
| Added profit vs V12 Final | $5,819.14 |
| Increase vs V12 profit | 181.76% |

## Drawdown estimate

Because the exact V12 trade ledger is not available in the working files or current branch search results, the combined drawdown cannot be proven as a true merged chronological drawdown. The report therefore shows a drawdown range:

| DD method | Estimate | Meaning |
|---|---:|---|
| Isolated/max-component DD | 7.94% | Assumes V12 and ICT drawdowns do not fully stack at the same time. |
| Conservative stacked stress DD | 13.19% | Adds V12 stress DD 5.25% + ICT DD 7.94%. |

The ICT stream itself reached max concurrent open risk of **0.90%** at 2026-03-12 14:22:00. This is below 1.00%, but when combined with V12 positions, a master governor is still required.

## Risk-capped combined profile

To keep a conservative stacked drawdown estimate below 9.50%, the ICT side can be scaled to **0.535x** of the current low_dd_active_gap60 risk.

| Metric | Risk-capped combined estimate |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $11,316.47 |
| Net result | $6,316.47 |
| Return | 126.33% |
| V12 component | $3,201.58 |
| Scaled ICT component | $3,114.89 |
| Estimated conservative stacked DD | 9.50% |

## Recommended combined architecture

The combined engine should not simply run both systems without control. Recommended priority:

1. V12 Final remains the master protected engine.
2. ICT low_dd_active_gap60 is added as a satellite intraday module.
3. V12 trades receive priority if the risk book is crowded.
4. ICT trades use normal risk only when combined open risk is acceptable.
5. ICT trades drop to micro-risk when the combined account drawdown reaches 5.00%.
6. A master total drawdown stop should remain under the prop-style 10% account threshold.

## Decision

The combined research estimate is positive. Full-size V12 + ICT produces **$9,020.72** net estimated profit, ending at **$14,020.72**, but conservative stacked drawdown may be around **13.19%**. The safer deployable research candidate is the risk-capped combined profile, ending at **$11,316.47** with estimated conservative DD near **9.50%**.

## Limitation

This is not a true merged chronological replay because the V12 accepted trade ledger is not available. A production-grade combined test requires exporting the V12 ledger with entry time, exit time, symbol, engine, side, risk, and pnl, then merging it with the ICT trade ledger and replaying through one portfolio governor.
