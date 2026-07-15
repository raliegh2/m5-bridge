@echo off
setlocal
cd /d "%~dp0"
title V14.3 Satellite Trading Bot

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
set "V14_3_LEGACY_GBP_ICT_PROVIDER=v14_3_signals"
call ".venv\Scripts\activate.bat"

echo Running MT5 preflight checks...
python v14_3_satellite_preflight.py
if errorlevel 1 (
    echo.
    echo Preflight failed. The bot was not started.
    echo Correct the reported MT5, account, symbol, or environment issue and retry.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting V14.3 Satellite Trading Bot with live GBP ICT provider...
python v14_3_satellite_bot_m1.py

if errorlevel 1 (
    echo.
    echo The bot exited with an error.
    pause
)

endlocal
