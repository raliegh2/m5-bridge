# V14.7 Combined V12 + ICT Backtest Result

Status: **COMPLETED IN GITHUB ACTIONS — RESEARCH/DEMO ONLY**

Workflow run: `29370297801`  
Artifact: `final-five-symbol-combined-validation`  
Artifact digest: `sha256:fb5cfa32728b29361f55bdaea40a5076ddb5500c6ed4d8cc44a16c684aee1a56`

## Result

| Metric | Result |
|---|---:|
| Starting balance | $5,000.00 |
| Ending balance | $14,836.61 |
| Net profit | $9,836.61 |
| Return | 196.73% |
| Closed trades | 4,755 |
| Skipped ICT trades | 1,101 |
| Profit factor | 1.1540 |
| Maximum closed-equity drawdown | 11.93% |

## Engine contribution

| Engine group | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| V12 | 918 | $3,201.58 | 1.6064 |
| ICT | 3,837 | $6,635.03 | 1.1132 |
| **Combined** | **4,755** | **$9,836.61** | **1.1540** |

## Symbol contribution

| Symbol | Trades | Net profit | Profit factor |
|---|---:|---:|---:|
| GBPUSD | 2,615 | $7,463.75 | 1.1579 |
| GBPJPY | 1,671 | $1,230.62 | 1.0901 |
| AUDUSD | 198 | $487.59 | 1.3512 |
| EURUSD | 157 | $442.19 | 1.4376 |
| USDJPY | 114 | $212.45 | 1.3833 |

## GBPJPY controls tested

- One unresolved GBPJPY ICT position maximum.
- 0.20% normal GBPJPY risk.
- 0.10% post-loss GBPJPY risk.
- Two-loss symbol stop.
- 0.50% symbol daily-loss cap.
- Four-hour rolling-loss cooldown.
- 07:00–20:00 UTC entry window.
- One new trade per symbol per hour.

## Data coverage and limitation

This is a chronological account-equity replay, but it is **not a common-period ten-year comparison of both engines**:

- V12 trades cover approximately February 2013 through March 2022.
- ICT trades cover January 2023 through July 2026.

The model therefore applies V12 history first and ICT history afterward. There is no overlapping V12/ICT period in this repository dataset, so the result measures sequential combined account performance rather than simultaneous interaction across the same ten-year market window.

The missing V14.3 selected ICT ledger was rebuilt deterministically from the committed `deduped_liquidity_fade_gap60.csv` source using the locked filters documented in the repository. The rebuild produced 4,938 ICT candidates from 11,649 committed source rows.

## Validation

- 35 focused tests passed.
- V12 repository-history replay passed.
- ICT ledger reconstruction passed.
- V14.7 chronological combined replay passed.
- Artifact upload passed.

Do not use this result as evidence for funded/live deployment. A true common-period merged replay still requires V12 and ICT candidate ledgers covering the same timestamps, followed by broker-native forward testing with spread, commission, swap and slippage.
