# V14.3 Locked Ruleset

Status: locked research ruleset for forward testing. Do not tune these rules using the same historical replay results.

## Engine roles

- V12 Final stays the master protected engine.
- ICT V14.3 is a satellite intraday module.
- V12 has priority over ICT when risk capacity is crowded.
- ICT can be disabled or reduced without modifying V12 Final.

## Locked ICT edge filters

The ICT satellite accepts only signals that pass all locked filters below.

| Rule | Locked action |
|---|---|
| GBPJPY breakout-fade setup family | Exclude |
| GBPUSD sweep_reclaim_15 setup family | Exclude |
| Tuesday entries | Exclude |
| 07:00 entry hour | Exclude |
| 13:00 entry hour | Exclude |
| Remaining sweep/reclaim and stronger liquidity-fade signals | Accept if risk governor allows |

Allowed input data for filtering:

- symbol;
- setup family;
- entry timestamp;
- weekday;
- hour;
- pre-entry risk-book state.

Disallowed input data:

- current trade result;
- later trades;
- future equity curve;
- future drawdown;
- full-period yearly result;
- any information not known before entry.

## Locked risk governor

| Setting | Value |
|---|---:|
| Active ICT risk | 0.40% |
| Micro ICT risk | 0.05% |
| Micro-risk trigger | 8.25% conservative combined drawdown proxy |
| Hard-stop trigger | 9.50% conservative combined drawdown proxy |
| V12 stress-DD reserve | 5.25% |
| Max ICT open risk | 0.75% |
| Max combined open risk target | 1.25% |

## Current research benchmark

| Metric | V14.3 research estimate |
|---|---:|
| Starting balance | $5,000.00 |
| Combined ending balance | $13,711.50 |
| Combined net result | $8,711.50 |
| Conservative stacked DD estimate | 9.48% |
| ICT accepted trades | 4,303 |

## Lock rule

No parameters in this file should be changed until the forward-test window is complete and reviewed. Any change creates a new version number and resets the validation clock.
