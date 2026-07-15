# V14.3 Strict Re-Split Walk-Forward Test Report

Status: **strict re-split test using uploaded-data candidate signal stream; ICT replay is chronological/no-lookahead; V12 ledger was not found, so V12 remains aggregate only**

## Ledger check

I searched the GitHub repo for a V12 final accepted-trade ledger using ledger/trade/equity/result terms and did not find a usable V12 chronological ledger. Therefore, this report validates the ICT satellite on a strict re-split and includes V12 only as a clearly labelled aggregate reference.

A true merged V12+ICT replay still requires a V12 ledger with entry time, exit time, symbol, side, risk, R/PnL, and equity/drawdown.

## Data and split

The available high-activity ICT candidate stream produced from the uploaded data covers **2023-01-02 to 2026-07-03** and contains **11,649** candidate signals.

| Split | Window | Purpose | Candidate signals |
|---|---|---|---:|
| Train | 2023-01-01 to 2023-12-31 | Filter selection | 3,357 |
| Confirm | 2024-01-01 to 2024-12-31 | Reject fragile filters | 3,343 |
| Locked test | 2025-01-01 to 2026-07-03 | One-time evaluation | 4,949 |

## No-future-knowledge procedure

The strict selector only sees train and confirm results. The locked-test window is evaluated after the filter set is selected. During replay, each accepted ICT trade is sized using only pre-trade realized equity, pre-trade drawdown proxy, and open-risk capacity. The current trade result is applied only after the trade is already accepted and sized.

## Walk-forward-selected filters

Selected using only 2023 train and 2024 confirm:

`exclude symbol GBPJPY; exclude hour 12:00; exclude hour 08:00; exclude setup sweep_reclaim_15`

## Locked-test results, ICT only

| Filter set | Accepted trades | Ending balance | Net result | Return | PF | ICT DD | Conservative stacked DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline all signals | 707 | $4,811.88 | $-188.12 | -3.76% | 0.849 | 4.27% | 9.52% |
| Historical V14.3 locked filters | 1,810 | $6,323.74 | $1,323.74 | 26.47% | 1.262 | 4.21% | 9.46% |
| Strict train/confirm-selected filters | 403 | $4,809.18 | $-190.82 | -3.82% | 0.715 | 4.28% | 9.53% |

## Aggregate V12 reference only, not a true merged replay

If the known V12 Final aggregate profit of **$3,201.58** is added as a non-chronological reference to the locked-test ICT result:

| Filter set | Reference ending balance | Reference net result | Reference return |
|---|---:|---:|---:|
| Historical V14.3 locked filters + V12 aggregate | $9,525.32 | $4,525.32 | 90.51% |
| Strict selected filters + V12 aggregate | $8,010.76 | $3,010.76 | 60.22% |

This aggregate reference should **not** be treated as a real combined chronological backtest.

## Interpretation

The historical V14.3 filter still performs positively on the stricter 2025-2026 locked segment, but it does **not** reproduce the full-period $13,711 result when the earlier years are withheld for train/confirm. The strict train/confirm-selected filter result is the more honest test because the test period was held back until after selection.

The practical conclusion is that V14.3 has useful edge, but the $13k result is not yet proven as a clean walk-forward result. To prove the combined system properly, export the V12 trade ledger and run both V12 and ICT through the same event-based portfolio replay.
