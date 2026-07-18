@echo off
setlocal
cd /d "%~dp0"
title V14.12 Net-Positive After-Cost Satellite Trading Bot

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: Python virtual environment was not found.
    echo Expected: %CD%\.venv\Scripts\python.exe
    echo Create and install the environment before starting the bot.
    echo.
    pause
    exit /b 1
)

set "PYTHONPATH=%CD%;%CD%\research"
set "V14_3_LEGACY_GBP_ICT_PROVIDER=v14_3_signals_research_parity"
rem Preserve the V14.4 state path so broker-net expectancy, equity peak, and
rem processed-deal history are not reset when upgrading to V14.12.
set "V14_3_LIVE_STATE_PATH=state/v14_4_profit_guard_live_state.json"
call ".venv\Scripts\activate.bat"

echo Running research-risk parity and broker compatibility preflight...
python v14_3_research_parity_preflight.py
if errorlevel 1 (
    echo.
    echo Preflight failed. The bot was not started.
    echo Correct the reported MT5, account, provider, or execution-gate issue and retry.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting V14.12 with retail-cost allocation and broker-net promotion gates...
python v14_4_satellite_bot.py

if errorlevel 1 (
    echo.
    echo The bot exited with an error.
    pause
)

endlocal
