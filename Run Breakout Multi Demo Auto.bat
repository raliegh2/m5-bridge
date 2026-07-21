@echo off
REM Multi-symbol V2 breakout: swing H4/D1 on GBPUSD/AUDUSD/EURUSD + intraday
REM M30/H4 on gold. Fails closed unless MT5 reports a DEMO account. Ctrl+C stops.
cd /d "%~dp0"
python -m mt5_ai_bridge.breakout_multi_runner
pause
