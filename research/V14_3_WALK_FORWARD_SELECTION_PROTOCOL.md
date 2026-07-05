# V14.3 Walk-Forward Selection Protocol

Status: protocol for future locked walk-forward validation.

## Goal

Prevent data snooping by selecting filters on one time period and evaluating them on a later untouched period.

## Required structure

Use rolling windows:

1. Train/select filters on an earlier window.
2. Confirm on the next window.
3. Test once on a later untouched window.
4. Do not change the selected rules after seeing the test result.

## Example window design

| Stage | Window | Purpose |
|---|---|---|
| Train | 2016-07-04 to 2021-12-31 | Candidate filter discovery |
| Confirm | 2022-01-01 to 2022-12-31 | Reject fragile filters |
| Locked test | 2023-01-01 to 2026-07-03 | One-time evaluation |
| Forward test | 2026-07-06 onward | Fresh unseen validation |

## Candidate filter limits

To avoid overfitting, the selector may test only a small number of simple filters:

- symbol;
- setup family;
- weekday;
- hour;
- session;
- max spread bucket;
- volatility bucket.

No filter may use future performance, full-period annual results, or any value that would not be known at trade entry.

## Acceptance rules

A filter set can be selected only if:

- it improves profit factor on train and confirm;
- it does not reduce accepted trades below the minimum activity threshold;
- it keeps conservative drawdown under the threshold;
- it does not depend on one year or one symbol only;
- it keeps positive or near-flat performance in the confirmation window.

## Locked-test rule

After a filter set is chosen, run it once on the locked test window. If it fails, the failure must be recorded. Do not keep changing the filter until the locked test passes.
