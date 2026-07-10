# V14.6 Profit-Preserving Combined Backtest Notes

## Why V14.6 Exists

V14.5 fixed the GBPJPY loss-cluster problem, but it cut profitability too hard. V14.6 is the profitability-preserving version. It keeps the V12 final ledger intact and applies a lighter safety overlay to the ICT satellite instead of globally suppressing the whole system.

## Design Choice

V14.6 does **not** use the very defensive V14.5 profile. The goal is to maintain most of the V14.3 upside while still preventing GBPJPY from repeatedly firing at full size after losses.

## V14.6 Controls

- V12 final trades remain unchanged.
- ICT default risk restored to 0.45%.
- ICT max risk remains 0.45%.
- ICT post-loss risk throttles to 0.35%, not 0.10%.
- GBPJPY risk is reduced only when GBPJPY is negative for the day.
- GBPJPY negative-day risk: 0.25%.
- GBPJPY positive-day risk: 0.35%.
- Profit lock is widened: trigger at +2.0% daily profit.
- Giveback stop is widened: 75% of peak daily realized profit.
- Equity high-watermark stop widened to 3.0% from day high.
- Symbol block after 4 consecutive losses, not 2.
- Symbol daily stop after 5 daily losses, not 2.
- Global pause after 4 consecutive losses, not 2.
- Global daily stop after 8 losses, not 3.
- Max 3 new trades per symbol per hour.
- Max 6 new ICT trades total per hour.
- Max 5 simultaneous ICT trades.

## Expected Behavior

V14.6 should be much more profitable than V14.5, but less aggressive than unrestricted V14.3. The expected trade-off is:

- More profit retained than V14.5.
- Less GBPJPY concentration risk than V14.3.
- More room for the strategy to recover after normal variance.
- Fewer premature daily shutdowns.

## How to Run

```powershell
cd C:\Users\ralie\mt5-ai-bridge
git pull
.\Run V14.6 Profit Preserving Combined Backtest.bat
```

Or manually:

```powershell
python research\v14_6_profit_preserving_combined_replay.py `
  --v12-ledger research\v12_final_ledger_output\v12_final_trade_ledger.csv `
  --ict-trades research\v14_3_under10_target_out\selected_under10_target_trades.csv `
  --out research\v14_6_profit_preserving_combined_out
```

## Output Files

- `research/v14_6_profit_preserving_combined_out/v14_6_profit_preserving_combined_summary.json`
- `research/v14_6_profit_preserving_combined_out/v14_6_profit_preserving_combined_trades.csv`
- `research/v14_6_profit_preserving_combined_out/v14_6_profit_preserving_combined_skipped_ict.csv`
- `research/v14_6_profit_preserving_combined_out/v14_6_profit_preserving_combined_events.csv`

## Important

This is still a backtest/replay model, not live proof. It should be forward-tested on demo before prop or live use.
