# V14.4 Train/Confirm-Only ICT Edge Rebuild Report

Status: **strict rebuild attempt using only 2023 train + 2024 confirm for selection; 2025-2026 remained locked until after each protocol was fixed**

## Objective

Rebuild the ICT edge without using the locked 2025-2026 results for selection. A profile is valid only if it passes the locked test after being selected from train/confirm rules.

## Data split

| Split | Window | Candidate signals | Purpose |
|---|---|---:|---|
| Train | 2023-01-01 to 2023-12-31 | 3357 | Build/select rules |
| Confirm | 2024-01-01 to 2024-12-31 | 3343 | Reject fragile rules |
| Locked test | 2025-01-01 to 2026-07-03 | 4949 | One-time validation |

## Validation gates

A rebuilt profile must pass all gates:

| Gate | Required |
|---|---:|
| Locked-test profit factor | >= 1.10 |
| Locked-test net result | > 0 |
| Conservative stacked drawdown proxy | <= 9.50% |
| Accepted locked-test trades | >= 200 |
| Selection uses locked-test data | No |

## Protocols tested

1. **A coarse symbol/setup whitelist** selected only buckets with positive train and confirm PF/avg R.
2. **A fine symbol/setup/hour whitelist** selected stronger intraday cells from train and confirm.
3. **A strict greedy exclusion profile** selected exclusions from train/confirm only.
4. **A symbol/setup/weekday stability selector** selected stable weekday cells from train and confirm.
5. **A single best train/confirm bucket selector** selected the highest-scoring bucket before the locked test.

## Locked-test results

| protocol                                |   selected_active_risk_percent |   locked_signals |   locked_accepted_trades |   locked_net_result |   locked_profit_factor |   locked_conservative_stacked_dd_percent | locked_passed_all_gates   |
|:----------------------------------------|-------------------------------:|-----------------:|-------------------------:|--------------------:|-----------------------:|-----------------------------------------:|:--------------------------|
| A_coarse_symbol_setup_whitelist         |                           0.25 |             1947 |                      530 |           -195.586  |               0.806929 |                                  9.50822 | False                     |
| B_fine_symbol_setup_hour_whitelist      |                           0.4  |              521 |                      521 |             11.1868 |               1.00999  |                                  9.44668 | False                     |
| C_strict_greedy_exclusion_train_confirm |                           0.3  |             1726 |                      484 |           -195.639  |               0.755808 |                                  9.50744 | False                     |
| D_symbol_setup_weekday_stability        |                           0.2  |             1140 |                     1140 |            -43.8899 |               0.982169 |                                  9.47776 | False                     |
| E_single_best_train_confirm_bucket      |                           0.4  |               59 |                       59 |             16.3221 |               1.02837  |                                  7.63793 | False                     |

## Decision

**No V14.4 train/confirm-only rebuild passed all locked-test gates.**

The strongest result by locked-test PF was **E_single_best_train_confirm_bucket**, but it still did not satisfy all gates:

| Metric | Value |
|---|---:|
| Selected active ICT risk | 0.400% |
| Locked accepted trades | 59 |
| Locked net result | $16.32 |
| Locked PF | 1.028 |
| Conservative stacked DD | 7.64% |
| Passed all gates | False |

## Important diagnostic

I also saved one **post-locked diagnostic candidate** that would pass the locked segment, but it is explicitly marked **not valid** because the bucket set was identified after inspecting locked-test diagnostics. It can only be used as a hypothesis for a new future split or forward test, not as proof on this split.

## Conclusion

The strict answer is: **the ICT edge was not successfully rebuilt into a clean valid V14.4 using only 2023 train and 2024 confirm data.** The prior V14.3 historical result still shows that useful edge exists, but under this stricter validation standard, the $13k-style result is not proven.

The correct next step is to either:

1. export the missing V12 ledger and run a real merged portfolio replay; or
2. define a new candidate from the diagnostic ideas, lock it, and test it on a new unseen period beyond 2026-07-03.
