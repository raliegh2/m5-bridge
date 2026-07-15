# V13 Do Not Merge Yet

This branch should remain draft/research until a true merged chronological replay
is completed.

The current result is an available-data estimate only. It should not be used as a
live, demo, or supervised execution result.

Required before merge:

1. V12 candidate ledger.
2. V11 intraday candidate ledger.

## True synchronized replay update

The raw-price synchronized replay in `v13_true_10y_combined_backtest.py` now
shows that the current V11 intraday extension reduces V12 Final profit by
$812.65 and increases drawdown. PR #36 must not be merged in its current form.
3. Same timestamp range.
4. Chronological merged replay.
5. V13 risk-governor pass/fail report.
6. Drawdown and PF review.
