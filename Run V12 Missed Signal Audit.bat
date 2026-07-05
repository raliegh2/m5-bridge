@echo off
setlocal
cd /d "%~dp0"

echo Starting read-only V12 missed-signal audit...
echo This does NOT submit orders and does NOT modify bot state.
echo.

set /p SINCE=Enter UTC since timestamp [leave blank for today's UTC midnight, example 2026-07-05T21:00:00Z]: 
set /p INTERVAL=Audit interval seconds [default 60]: 
if "%INTERVAL%"=="" set INTERVAL=60

if "%SINCE%"=="" (
  python research\audit_today_missed_signals.py --loop --interval %INTERVAL%
) else (
  python research\audit_today_missed_signals.py --since "%SINCE%" --loop --interval %INTERVAL%
)

pause
