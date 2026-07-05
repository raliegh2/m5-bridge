# V14.1 Dynamic Profit-Scaling Combined System Report

Status: **research replay; ICT satellite is chronological/no-lookahead; V12 side remains aggregate because the exact V12 trade ledger is unavailable**

## Rule set implemented

| Rule | Value |
|---|---:|
| Base ICT scale | 0.535x |
| Max profit-boost scale | 0.85x |
| Boost condition | Pre-trade equity must be at/near high-water mark and above starting balance |
| Profit target for max boost | 100% account growth |
| Reduce to base | At 5.00% ICT satellite drawdown |
| Micro-risk trigger | Conservative combined proxy near 7.75% |
| Hard stop | Before 9.50% conservative combined proxy |
| V12 stress-DD assumption | 5.25% |

Because the exact V12 chronological ledger is unavailable, the combined-drawdown proxy uses V12 stress DD plus live ICT satellite DD. That means the satellite goes to micro-risk when its own DD is about 2.50% and hard-stops before about 4.25% ICT DD.

## No-future-knowledge validation

The ICT satellite replay is processed in entry-time order. For each trade, risk is assigned before the trade result is known, using only pre-trade equity, pre-trade drawdown, high-water status, and fixed thresholds. The current trade's R-result is applied only after the position size is assigned.

## ICT satellite result

| Metric | Prior V14 risk-capped | V14.1 dynamic selected |
|---|---:|---:|
| Starting balance | $5,000.00 | $5,000.00 |
| Ending balance | $7,591.19 | $7,695.19 |
| Net result | $2,591.19 | $2,695.19 |
| Return | 51.82% | 53.90% |
| Trades | 11,649 | 11,649 |
| Win rate | 47.75% | 47.75% |
| Profit factor | 1.120 | 1.125 |
| ICT max DD | 4.32% | 4.21% |
| Conservative stacked DD | 9.57% | 9.46% |
| Average ICT risk | 0.062% | 0.062% |
| Profit-boost trades | 0 | 456 |
| Micro-risk trades | 7,906 | 7,471 |
| Hard-stop skipped trades | 0 | 0 |

## Combined estimate

| Metric | V14 risk-capped combined | V14.1 dynamic combined |
|---|---:|---:|
| Starting balance | $5,000.00 | $5,000.00 |
| Ending balance | $10,792.77 | $10,896.77 |
| Net result | $5,792.77 | $5,896.77 |
| Return | 115.86% | 117.94% |
| Combined trades | 12,567 | 12,567 |
| Conservative stacked DD | 9.57% | 9.46% |

## Yearly ICT satellite result

| year | trades | total_r | pnl | avg_r | avg_risk | ending_equity |
|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 3357 | 167.549 | $1,768.20 | 0.0499 | 0.1149% | $6,768.20 |
| 2024 | 3343 | 75.859 | $505.79 | 0.0227 | 0.0602% | $7,273.99 |
| 2025 | 3263 | -23.779 | -$28.02 | -0.0073 | 0.0161% | $7,245.97 |
| 2026 | 1686 | 115.518 | $449.21 | 0.0685 | 0.0524% | $7,695.19 |

## Decision

V14.1 dynamic profit-scaling slightly improves the risk-capped combined profile while keeping conservative stacked drawdown under 9.50%. The selected version raises combined ending balance from **$10,792.77** to **$10,896.77**, but the gain is intentionally modest because the hard drawdown cap prevents aggressive scaling.

## Limitation

This is still not a true merged chronological V12+ICT replay because the exact V12 accepted-trade ledger is unavailable. A production-grade replay requires exporting the V12 ledger and running both engines through one unified risk governor.
