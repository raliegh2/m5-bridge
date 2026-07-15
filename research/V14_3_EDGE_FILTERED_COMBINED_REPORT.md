# V14.3 Edge-Filtered ICT Satellite + V12 Combined Research Report

Status: **strategy-edge research replay; ICT trades are replayed chronologically/no-lookahead; V12 remains aggregate because its exact ledger is unavailable**

## Objective

The V14.2 risk-scaling system protected drawdown but could not reach the $13,000 target because the 9.25% conservative drawdown cap forced too many micro-risk and skip decisions. V14.3 improves the **strategy edge** by reducing low-expectancy ICT signals before risk is applied.

## Edge filter added

The V14.3 ICT satellite keeps only the stronger liquidity-fade/reclaim buckets and removes weaker/noisier buckets:

| Filter | Action |
|---|---|
| GBPJPY breakout fades | Excluded |
| GBPUSD sweep_reclaim_15 | Excluded |
| Tuesday trades | Excluded |
| 07:00 hour trades | Excluded |
| 13:00 hour trades | Excluded |
| Remaining sweep/reclaim and stronger fade signals | Accepted |

These filters use only information known at signal time: symbol, setup family, weekday, and hour. The replay then sizes each accepted trade using only pre-trade equity/drawdown information.

## Risk settings used

| Setting | Value |
|---|---:|
| Active ICT risk | 0.40% |
| Micro ICT risk | 0.05% |
| Micro-risk trigger | 8.25% conservative combined DD proxy |
| Hard-stop trigger | 9.50% conservative combined DD proxy |
| V12 stress-DD reserve | 5.25% |
| Max ICT open risk | 0.75% |

## No-future-knowledge validation

The ICT satellite replay is processed in entry-time order. A trade is filtered using only its own pre-entry attributes. Position size is assigned before the current trade's R-result is applied. No future trade result is used to decide whether to enter, skip, or resize the trade.

Important: this is still a **research candidate**, not production proof, because the filter set was designed after reviewing prior historical behavior. A clean production proof still requires fresh forward validation or a locked walk-forward protocol.

## Result comparison

| Scenario | Ending balance | Net result | Return | Trades | PF | Conservative DD |
|---|---:|---:|---:|---:|---:|---:|
| V12 Final aggregate only | $8,201.58 | $3,201.58 | 64.03% | 918 | 1.606 | 5.25% |
| V14.2 strict profit-cushion combined | $10,277.32 | $5,277.32 | 105.55% | 9,389 | — | 9.25% |
| V14.3 edge-filtered ICT satellite only | $10,509.92 | $5,509.92 | 110.20% | 4,303 | 1.238 | 9.48% |
| V14.3 V12 + edge-filtered ICT estimate | $13,711.50 | $8,711.50 | 174.23% | 5,221 | — | 9.48% |

## Target result

V14.3 reaches the $13,000 target in the combined estimate:

| Metric | Value |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $13,711.50 |
| Net result | $8,711.50 |
| Return | 174.23% |
| Conservative stacked DD estimate | 9.48% |

## ICT yearly result

|   year |   signals |   accepted |   total_r |        pnl |     avg_r |   avg_risk |
|-------:|----------:|-----------:|----------:|-----------:|----------:|-----------:|
|   2023 |      1262 |       1262 |  134.807  | 2451.46    | 0.10682   |  0.258954  |
|   2024 |      1231 |       1231 |   63.5161 |  962.535   | 0.0515971 |  0.165435  |
|   2025 |      1179 |       1179 |   56.3216 |    6.37602 | 0.0477707 |  0.0569975 |
|   2026 |       631 |        631 |   67.2562 | 2089.55    | 0.106587  |  0.181696  |

## Decision

V14.3 improves edge rather than relying only on risk scaling. It reduces the trade stream from 11,649 ICT signals to 4,303 higher-quality accepted ICT trades and raises the combined estimate to **$13,711.50** while keeping the conservative stacked DD estimate under 9.50%.

## Limitations

1. This is not a true merged V12+ICT chronological replay because the exact V12 accepted-trade ledger is unavailable.
2. The V14.3 edge filter was selected after reviewing prior historical behavior, so it must be treated as a research candidate.
3. Before live use, lock the filter rules and test forward or rerun with a proper walk-forward selection protocol.
