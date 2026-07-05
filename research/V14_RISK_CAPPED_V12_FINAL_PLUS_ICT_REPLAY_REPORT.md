# V14 Risk-Capped V12 Final + ICT Satellite Replay

Status: **risk-capped combined research replay; ICT satellite replay is chronological/no-lookahead; exact V12 merge limited by missing V12 trade ledger**

## What was tested

- V12 Final remains the master protected engine.
- ICT `low_dd_active_gap60` is treated as the satellite intraday module.
- ICT risk is scaled to **0.535x** of the current low-DD ICT profile.
- ICT trades drop to scaled micro-risk if the pre-trade account drawdown proxy is already at or above **5.00%**.
- ICT trade sizing uses only information known **before** each trade.

## No-future-knowledge rule

The ICT chronological replay processes trades in entry-time order. For each trade, the assigned risk is calculated from:

1. the original low-DD ICT mode available before that trade;
2. the fixed risk cap scale;
3. the account drawdown before the trade;
4. the master micro-risk trigger.

The current trade result is applied only **after** the position is accepted and sized. No future trade result is used to decide whether to enter or how large to trade.

## V12 limitation

The exact V12 accepted-trade ledger was not available in the working files or branch search. Therefore, V12 is included using the known V12 Final aggregate result. A true merged chronological replay requires the V12 ledger with entry time, exit time, symbol, engine, side, risk, and PnL.

## Component result

| Component | Net result | Ending balance | Return | Trades | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| V12 Final protected engine | $3,201.58 | $8,201.58 | 64.03% | 918 | 1.606 | 4.93% |
| ICT risk-capped satellite | $2,591.19 | $7,591.19 | 51.82% | 11,649 | 1.120 | 4.32% |

## Combined estimate

| Metric | Result |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $10,792.77 |
| Net result | $5,792.77 |
| Return | 115.86% |
| Combined trades | 12,567 |
| Isolated/max-component DD | 4.93% |
| Conservative stacked stress DD | 9.57% |

## ICT risk-capped details

| Metric | Result |
|---|---:|
| Risk scale | 0.535x |
| Average ICT risk | 0.062% |
| Max ICT risk | 0.161% |
| Min ICT risk | 0.016% |
| Active-risk trades | 3,743 |
| Micro-risk trades | 7,906 |
| Master-DD micro-risk trades | 0 |
| Win rate | 47.75% |

## Yearly ICT satellite result

| year | trades | total_r | pnl | avg_r | avg_risk |
|---:|---:|---:|---:|---:|---:|
| 2023 | 3357 | 167.549 | $1,676.17 | 0.0499 | 0.1136% |
| 2024 | 3343 | 75.859 | $575.64 | 0.0227 | 0.0601% |
| 2025 | 3263 | -23.779 | -$27.94 | -0.0073 | 0.0161% |
| 2026 | 1686 | 115.518 | $367.32 | 0.0685 | 0.0554% |

## Decision

The risk-capped combined profile remains positive. It estimates **$5,792.77** net result on a $5,000 starting balance, ending at **$10,792.77**, while keeping conservative stacked drawdown near **9.57%**. This is safer than the full-size combination, but it is still not production proof until the V12 chronological trade ledger is exported and merged with the ICT ledger.
