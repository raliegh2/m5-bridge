@echo off
REM Double-click to start the MT5 AI Bridge bot.
REM It opens a dedicated console window (logs) and serves the live dashboard
REM website at http://127.0.0.1:8800, opening your browser to it.
REM To stop the bot: press Ctrl+C in its console window (or close the window).
cd /d "%~dp0"
start "MT5 AI Bridge Bot" cmd /k python bridge.py
