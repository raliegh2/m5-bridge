# V14.2 Profit-Cushion Scaling Combined System Report

Status: **research replay; ICT satellite is chronological/no-lookahead; V12 remains aggregate because the exact V12 trade ledger is unavailable**

## Rule set implemented

| Rule | Value |
|---|---:|
| V12 role | Master protected engine |
| ICT role | Satellite intraday module |
| Base ICT scale | 0.535x |
| Profit scale 1 | 0.70x after ICT satellite equity > $6,000 |
| Profit scale 2 | 0.85x after ICT satellite equity > $7,000 |
| Profit scale 3 | 1.00x after ICT satellite equity > $8,500 |
| Reduce to base | At 5.00% conservative drawdown proxy |
| Micro-risk | At 7.50% conservative drawdown proxy |
| Hard stop / skip new ICT | Before 9.25% conservative drawdown proxy |
| Max combined open risk | 1.25% |
| Max ICT open risk | 0.75% |
| V12 stress-DD reserve | 5.25% |

Because the V12 chronological trade ledger is unavailable, ICT scaling uses **ICT satellite equity only** and does not use future V12 aggregate profit. Conservative drawdown is estimated as V12 stress DD plus live ICT satellite DD.

## No-future-knowledge validation

The ICT satellite replay is processed in entry-time order. For every trade, the system calculates scale, drawdown state, and open-risk capacity before the trade result is applied. The current trade result is applied only after sizing is fixed. No future trade result is used to decide whether to enter, scale, micro-risk, or skip.

## V14.2 selected result

| Metric | V14.1 dynamic | V14.2 profit-cushion |
|---|---:|---:|
| ICT ending balance | $7,695.19 | $7,075.74 |
| ICT net result | $2,695.19 | $2,075.74 |
| ICT return | 53.90% | 41.51% |
| ICT accepted trades | 11,649 | 8,471 |
| ICT zero/skip trades | 0 | 3,178 |
| Profit factor | 1.125 | 1.130 |
| ICT max DD | 4.21% | 4.00% |
| Conservative stacked DD | 9.46% | 9.25% |
| Avg ICT risk | 0.062% | 0.048% |
| Max ICT risk | — | 0.161% |
| 0.70x boost trades | — | 0 |
| 0.85x boost trades | — | 0 |
| 1.00x boost trades | — | 0 |
| Micro-risk trades | — | 5,391 |
| Open-risk reduced trades | — | 0 |

## Combined estimate

| Metric | V14.1 combined | V14.2 combined |
|---|---:|---:|
| Starting balance | $5,000.00 | $5,000.00 |
| Ending balance | $10,896.77 | $10,277.32 |
| Net result | $5,896.77 | $5,277.32 |
| Return | 117.94% | 105.55% |
| Combined accepted trades | 12,567 | 9,389 |
| Conservative stacked DD | 9.46% | 9.25% |

## Yearly ICT satellite result

|   year |   trades |   accepted_trades |   total_r |       pnl |       avg_r |   avg_risk |   ending_equity |
|-------:|---------:|------------------:|----------:|----------:|------------:|-----------:|----------------:|
|   2023 |     3357 |              3357 |  167.549  | 1712.25   |  0.0499103  | 0.113168   |         6712.25 |
|   2024 |     3343 |              3343 |   75.859  |  457.745  |  0.0226919  | 0.0460807  |         7169.99 |
|   2025 |     3263 |              1771 |  -23.7791 |  -94.2511 | -0.00728749 | 0.00871117 |         7075.74 |
|   2026 |     1686 |                 0 |  115.518  |    0      |  0.068516   | 0          |         7075.74 |

## Risk-reason summary

| risk_reason              |   trades |      pnl |   avg_risk |
|:-------------------------|---------:|---------:|-----------:|
| micro_at_7_5_dd          |     5391 |  -85.999 |   0.01605  |
| hard_stop_before_9_25_dd |     3178 |    0     |   0        |
| base_at_5_dd             |     3080 | 2161.74  |   0.154497 |

## Decision

V14.2 increases scaling rules but, under the strict combined drawdown proxy, it does **not** reach a $13,000 ending balance. The selected V14.2 profile ends at **$10,277.32** combined estimate, with conservative stacked DD at **9.25%**. The $13,000 target requires either an exact V12 ledger so scaling can use real combined equity without future leakage, a higher allowed drawdown ceiling, or additional strategy edge rather than only risk scaling.

## Limitation

This is still not a true merged chronological V12+ICT replay because the exact V12 accepted trade ledger is unavailable. A production-grade replay requires exporting the V12 ledger and running both engines through one unified risk governor.
