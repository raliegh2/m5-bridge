# True V13 Combined Backtest Requirements

The available-data estimate in this branch is positive, but it is not the final
combined backtest. The true V13 test requires merged chronological candidate
ledgers.

## Required input files

```text
research/v12_final_accepted_candidates.csv
research/v12_final_rejected_candidates.csv
research/v11_intraday_accepted_candidates.csv
research/v11_intraday_rejected_candidates.csv
```

## Required shared columns

```text
entry_time,exit_time,symbol,engine,setup,side,risk_percent,r_multiple
```

Optional but preferred:

```text
spread_pips,stop_pips,target_pips,quality_score,source_priority
```

## Replay order

1. Load all V12 and V11 candidates.
2. Normalize engine names to V13 naming.
3. Sort by `entry_time`, then `source_priority`, then candidate id.
4. Process exits before same-timestamp entries.
5. Apply V12/V13 risk governor:
   - max positions;
   - max open risk;
   - symbol caps;
   - GBP correlation caps;
   - duplicate checks;
   - spread checks;
   - adaptive guard state.
6. Record accepted/rejected trades and reasons.
7. Recalculate equity, realized drawdown, stress drawdown and PF.

## Required output metrics

| Metric | Required |
|---|---|
| Net profit | Yes |
| Ending balance | Yes |
| Return percent | Yes |
| Trade count | Yes |
| Win rate | Yes |
| Profit factor | Yes |
| Max drawdown | Yes |
| Stress drawdown | Yes |
| Rejected-by-reason table | Yes |
| Profit by engine | Yes |
| Profit by symbol | Yes |
| V11 accepted versus rejected count | Yes |
| V12 opportunity-cost analysis | Yes |

## Approval standard

The V13 profile should not move beyond research unless the true combined replay
beats V12 Final alone while keeping drawdown, stress drawdown and correlation
risk within acceptable bounds.
