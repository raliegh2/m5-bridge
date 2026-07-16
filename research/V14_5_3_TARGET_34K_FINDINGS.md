# V14.5.3 — $34,000 Target Findings

## Scope

The exact synchronized ten-year period was July 3, 2016 through July 3, 2026, starting with $5,000. The target was $34,000 net profit, equivalent to a $39,000 ending balance.

The search retained the V14.5.2 signals, validated time filters, entries, exits, stops, targets, ICT controls, observation streams and 7.5/8.5/9.0/9.6 drawdown governor. Only promoted V12 risk was varied from 0.75% through 6.00% in 0.05-point increments.

## Current V14.5.2

| Costs | Risk | Net profit | Ending balance | Profit factor | Closed DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|
| Zero | 0.75% | $5,186.64 | $10,186.64 | 1.5997 | 6.4415% | 7.1774% |
| Demo | 0.75% | $4,140.56 | $9,140.56 | 1.4738 | 6.6221% | 7.3890% |
| Retail | 0.75% | $3,505.36 | $8,505.36 | 1.3980 | 6.7151% | 7.5023% |

## Best result retaining the existing drawdown boundary

The preserved boundary was no more than 9.6% closed drawdown and 10.0% stressed drawdown.

| Costs | Risk | Net profit | Ending balance | Profit factor | Closed DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|
| Zero | 1.05% | $7,833.95 | $12,833.95 | 1.6493 | 8.9346% | 9.9079% |
| Demo | 1.00% | $5,964.94 | $10,964.94 | 1.5200 | 8.7485% | 9.7149% |
| Retail | 1.00% | $5,150.54 | $10,150.54 | 1.4474 | 8.8648% | 9.8529% |

## Highest net profit found, including results outside the stress boundary

| Costs | Risk | Net profit | Ending balance | Profit factor | Closed DD | Stress DD |
|---|---:|---:|---:|---:|---:|---:|
| Zero | 1.15% | $8,614.71 | $13,614.71 | 1.6515 | 9.5679% | 10.4523% |
| Demo | 1.10% | $6,600.64 | $11,600.64 | 1.5254 | 9.4078% | 10.3508% |
| Retail | 1.10% | $5,718.43 | $10,718.43 | 1.4542 | 9.5317% | 10.5328% |

## Feasibility conclusion

No promoted-risk value from 0.75% through 6.00% reached $34,000 net profit under zero-cost, demo-cost or retail-cost assumptions. Increasing risk beyond the profit-maximizing area caused the drawdown hard stop to block most later trades and reduced, rather than increased, total profit.

Therefore the $34,000 target cannot be reached by position-size scaling while preserving the V14.5.2 trade stream and drawdown architecture. It requires additional validated trading edge: new profitable engines, additional independent markets or timeframes, materially improved exits, or another source of positive cost-adjusted expectancy. Merely increasing risk is rejected by the backtest evidence.

Research only. The replay uses fixed R-based costs rather than tick-level broker execution, and historical results do not guarantee future profitability.
