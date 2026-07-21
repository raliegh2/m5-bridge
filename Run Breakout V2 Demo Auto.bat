@echo off
REM Multi-symbol portfolio plus validated GBPUSD Breakout V2. It fails closed
REM unless MT5 explicitly reports a DEMO account. Press Ctrl+C to stop.
cd /d "%~dp0"
python -m mt5_ai_bridge.breakout_v2_runner
pause
