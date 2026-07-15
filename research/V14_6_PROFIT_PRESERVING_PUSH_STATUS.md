# V14.6 Profit-Preserving Push Status

## Branch

`v13-v12-final-plus-v11-intraday`

## Pushed V14.6 Files

- `research/v14_6_profit_preserving_combined_replay.py`
- `research/V14_6_PROFIT_PRESERVING_COMBINED_BACKTEST_NOTES.md`
- `Run V14.6 Profit Preserving Combined Backtest.bat`
- `.github/workflows/v14-6-profit-preserving-combined-backtest.yml`

## Required Data for Exact Chronological Combined Backtest

The exact V12 + ICT combined replay requires:

- `research/v12_final_ledger_output/v12_final_trade_ledger.csv`
- `research/v14_3_under10_target_out/selected_under10_target_trades.csv`

The V12 ledger is already present in this branch. The ICT selected trade stream must exist at the path above before the exact combined replay can run.

## Run Command

```powershell
python research\v14_6_profit_preserving_combined_replay.py `
  --v12-ledger research\v12_final_ledger_output\v12_final_trade_ledger.csv `
  --ict-trades research\v14_3_under10_target_out\selected_under10_target_trades.csv `
  --out research\v14_6_profit_preserving_combined_out
```

## Purpose

V14.6 is the profitability-preserving version after V14.5 overprotected the account and reduced upside. It keeps V12 intact, restores ICT risk closer to V14.3, and uses lighter GBPJPY throttling and symbol-level protection instead of globally suppressing the system.
