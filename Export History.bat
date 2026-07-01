@echo off
REM Double-click to export M5 history (SYMBOL from .env) to a CSV for backtesting.
REM MetaTrader 5 must be open and logged in.
cd /d "%~dp0"
python -m mt5_ai_bridge.export_history
echo.
pause
