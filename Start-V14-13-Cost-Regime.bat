@echo off
setlocal
cd /d "%~dp0"
title V14.13 Cost-Regime Research-Parity Trading Bot

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: Python virtual environment was not found.
    echo Expected: %CD%\.venv\Scripts\python.exe
    echo.
    pause
    exit /b 1
)

set "PYTHONPATH=%CD%;%CD%\research"
set "V14_3_LEGACY_GBP_ICT_PROVIDER=v14_3_signals_research_parity"
set "V14_3_LIVE_STATE_PATH=state/v14_4_profit_guard_live_state.json"
call ".venv\Scripts\activate.bat"

 echo Running V14.3 research-parity preflight...
python v14_3_research_parity_preflight.py
if errorlevel 1 (
    echo.
    echo Preflight failed. The bot was not started.
    echo.
    pause
    exit /b 1
)

 echo.
echo Starting V14.13 cost-regime model...
python v14_4_satellite_bot.py

if errorlevel 1 (
    echo.
    echo The bot exited with an error.
    pause
)

endlocal
