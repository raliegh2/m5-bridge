@echo off
setlocal
cd /d "%~dp0"

echo Running V14.5 protected forward-test replay...
echo This is research-only. It does NOT connect to MT5 and does NOT submit orders.
echo.

python research\v14_5_protected_forward_model_replay.py --ict-trades research\v14_3_under10_target_out\selected_under10_target_trades.csv --out research\v14_5_protected_forward_model_out

echo.
echo Output folder: research\v14_5_protected_forward_model_out
pause
