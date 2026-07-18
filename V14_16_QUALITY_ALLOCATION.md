# V14.16 Cost-Efficient Quality Allocation

V14.16 addresses the large difference between the V14.3 zero-cost benchmark and cost-adjusted results by allocating more of the existing risk budget to the strongest cost-resilient profiles. It does not assume transaction costs disappear.

## Quality allocation

A candidate may use the existing 0.80% single-trade ceiling only when:

1. V14.15 already funds it at full strength;
2. it is not an observation, probation, pressure-reduced, expectancy-reduced or drawdown-reduced candidate;
3. it belongs to a frozen quality profile;
4. its modeled cost is inside the profile limit;
5. the 1.75% ICT and 3.25% combined risk caps still admit it.

The frozen profiles are:

- GBPUSD V10 Precision;
- strict cost-qualified GBPUSD ICT;
- EURUSD Swing Core;
- EURUSD ICT Liquidity;
- AUDUSD Trend Pullback;
- AUDUSD ICT excluding the weak 10:00 UTC concentration.

GBPJPY and USDJPY remain at their V14.15 allocations because their evidence does not justify the same uplift.

## Live evidence boundary

The exact historical replay can test the frozen profiles. Live uplift additionally requires:

- at least 12 reconciled broker-net trades for the engine;
- at least 16 reconciled broker-net trades for the symbol/mode sleeve;
- mean broker-net result of at least +0.10R for both;
- profit factor of at least 1.15 for both.

Before those conditions are met, the live executor preserves V14.15 risk.

## Retained safety controls

- 0.80% single-trade ceiling;
- 1.75% ICT open-risk ceiling;
- 3.25% combined open-risk ceiling;
- completed-candle/no-lookahead entries;
- transaction-cost, spread and staleness guards;
- loss-pressure and expectancy reductions;
- 7.5/8.5/9.0/9.6% drawdown governor;
- demo-only transmitted execution.

This branch is research and demo-forward only. Historical modeled results do not guarantee future performance.
