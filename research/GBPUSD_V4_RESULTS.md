# GBPUSD V4 research results

Dataset: GBPUSD H4, 2016-01-04 through 2026-07-01. Starting balance $5,000. Risk 0.35% per trade. Costs include the MT5 spread field with a 0.8-pip minimum, 0.3-pip slippage per execution side, and a -0.2-pip-per-day swap proxy.

Full result: ending balance $5,537.57; net +$537.57; 99 trades; profit factor 2.14; maximum mark-to-market drawdown 1.74%; win rate 68.69%; daily Sharpe proxy 0.79.

Development 2016-2021: 61 trades, +$385.71, PF 2.46, max drawdown 1.74%.
Later validation 2022-July 2026: 38 trades, +$161.90, PF 1.89, max drawdown 1.31%.
July 2024-July 2026: 15 trades, +$114.22, PF 2.64, max drawdown 0.89%.

Stress costs of a 2.0-pip spread floor and 1.0-pip slippage produced +$420.72, PF 1.83, and 1.76% maximum drawdown.

The strategy was negative in the independent 2016 and 2023 calendar segments. It is a selected historical research candidate, not a guarantee. The historical news filter was not tested because no event dataset was supplied.

The 0.35% run produced only eight realized-event days at or above 0.5% of the original balance, and the maximum gap between entries was 204 days. It is not designed to produce three qualifying days each week or solve a 30-day inactivity requirement.
