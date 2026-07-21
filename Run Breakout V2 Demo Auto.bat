@echo off
REM Validated GBPUSD Breakout V2 automated runner. It fails closed unless MT5
REM explicitly reports a DEMO account. Press Ctrl+C to stop.
cd /d "%~dp0"
python -m mt5_ai_bridge.breakout_v2_runner
pause
