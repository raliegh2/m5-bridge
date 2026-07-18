# V14.13 Cost-Regime Research Parity

V14.13 preserves the documented V14.3 strategy identity and setup-specific maximum risk when transaction cost is zero or exceptionally low. It does not replace the GBP liquidity model with a swing-only portfolio.

## Decision hierarchy

1. Calculate all-in entry cost in R from live spread, commission reserve, slippage reserve, non-M1 swap reserve and latency reserve.
2. Reject any trade whose cost exceeds 0.18R or consumes more than 15% of the planned target.
3. Preserve robust V12 and EURUSD/AUDUSD satellite sleeves after costs.
4. At medium costs, retain the broad GBPJPY sweep-reclaim-15 sleeve and keep other non-core GBP candidates at observation risk.
5. At retail/stressed costs, fund only frozen GBP setup/time/side subsets that were positive after costs in each completed 2023, 2024 and 2025 block.
6. Hold cost-negative USDJPY, weak V12 and non-core high-cost GBP candidates in shadow without transmitting an order.
7. Apply every inherited V14.4 and V14.3 spread, staleness, expectancy, loss, exposure and drawdown control afterward.

## Unchanged strategy behavior

- completed-candle signal generation and no-lookahead policy;
- GBP M1 sweep/reclaim and breakout-fade entries;
- H1/H4/D1 V12 and satellite strategies;
- original structural stop and full-position target logic;
- setup-specific risk ceilings;
- 1.75% ICT and 3.25% combined admission caps;
- 7.5/8.5/9.0/9.6 drawdown governor;
- broker-native sizing rounded downward;
- demo-only transmitted AUTO execution.

## Validation boundary

The repository workflow compares current V14.3 and V14.13 over the same exact ten-year candidate chronology under:

- zero cost: 0R V12 / 0R ICT;
- demo cost: 0.02R V12 / 0.075R ICT;
- retail cost: 0.03R V12 / 0.13R ICT;
- stress cost: 0.05R V12 / 0.18R ICT.

Zero-cost results are a reproducibility benchmark, not a live assumption. Historical results do not guarantee future returns. Keep the model in READ_ONLY, then controlled demo-forward testing, until broker-specific costs and reconciliation are verified.
