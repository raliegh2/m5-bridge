@echo off
setlocal
cd /d "%~dp0"
title V14.21 Demo-Only Automatic Trading Bot

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: Python virtual environment was not found.
    echo Expected: %CD%\.venv\Scripts\python.exe
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo ERROR: .env was not found.
    echo Copy .env.v14-21-demo-auto.example to .env and add DEMO credentials.
    echo Never place funded or live credentials in this runner.
    echo.
    pause
    exit /b 1
)

set "PYTHONPATH=%CD%;%CD%\research"
set "V14_3_LEGACY_GBP_ICT_PROVIDER=v14_3_signals_research_parity"
call ".venv\Scripts\activate.bat"

echo Running V14.21 pinned-demo AUTO preflight...
python v14_21_demo_auto_preflight.py
if errorlevel 1 (
    echo.
    echo Preflight failed. No runner was started and no order was sent.
    echo Correct the reported demo account, terminal, data, gate, or kill-switch issue.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting V14.21 demo-only automatic runner...
python v14_21_demo_auto_runner.py

if errorlevel 1 (
    echo.
    echo The V14.21 runner exited with an error.
    pause
)

endlocal
