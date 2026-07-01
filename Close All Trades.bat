@echo off
REM Double-click to close ALL open positions on your MT5 account.
REM Closing reduces exposure. If the bot is running it may open new trades on
REM its next loop -- stop the bot first if you want to stay flat.
cd /d "%~dp0"
python -m mt5_ai_bridge.flatten
echo.
pause
