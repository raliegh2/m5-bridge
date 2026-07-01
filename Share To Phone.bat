@echo off
REM Shows the URL to open the live dashboard on your phone (via Tailscale).
REM The bot must be running with DASHBOARD_HOST=0.0.0.0, and Tailscale must be
REM ON on both this PC and your phone.
setlocal
set "TS=C:\Program Files\Tailscale\tailscale.exe"
set "TSIP="
if exist %TS% (
  for /f "tokens=*" %%i in ('%TS% ip -4 2^>nul') do set "TSIP=%%i"
) else (
  for /f "tokens=*" %%i in ('tailscale ip -4 2^>nul') do set "TSIP=%%i"
)
echo.
if "%TSIP%"=="" (
  echo Could not read your Tailscale IP.
  echo Make sure Tailscale is installed and running, then try again.
) else (
  echo ============================================================
  echo  Open this on your phone ^(with Tailscale toggled ON^):
  echo.
  echo      http://%TSIP%:8801
  echo.
  echo  Tip: confirm it works on THIS PC first at  http://127.0.0.1:8801
  echo ============================================================
)
echo.
pause
