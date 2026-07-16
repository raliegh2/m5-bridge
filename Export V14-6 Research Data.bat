@echo off
REM Export D1/H4/H1 for all five symbols and deep M1 for GBPUSD/GBPJPY
REM into research\data_v14_6\ for the full 10-year model rebuild.
REM MetaTrader 5 must be open and logged in.
REM Tip: Tools -> Options -> Charts -> "Max bars in chart" = Unlimited.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
set "PYTHONPATH=%CD%"
python tools\v14_6_export_full_history.py
echo.
pause
