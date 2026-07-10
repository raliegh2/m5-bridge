@echo off
setlocal
cd /d "%~dp0"

echo Running V14.6 profit-preserving V12 + ICT combined replay...
echo Research-only. This does NOT connect to MT5 and does NOT submit orders.
echo.

python research\v14_6_profit_preserving_combined_replay.py ^
  --v12-ledger research\v12_final_ledger_output\v12_final_trade_ledger.csv ^
  --ict-trades research\v14_3_under10_target_out\selected_under10_target_trades.csv ^
  --out research\v14_6_profit_preserving_combined_out

echo.
echo Output folder: research\v14_6_profit_preserving_combined_out
pause
