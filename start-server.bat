@echo off
rem Double-click: stop old EDM-AI server, start with LAN access, show the address.  Reset data: start-server.bat --reset
cd /d "%~dp0"
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'edm_ai' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
rem Firewall rule (works only when run as administrator; ignored otherwise)
netsh advfirewall firewall add rule name="EDM-AI" dir=in action=allow protocol=TCP localport=9101-9120 >nul 2>&1
set PYCMD=
py -3 -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set PYCMD=py -3
if not defined PYCMD python -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set PYCMD=python
if not defined PYCMD (
  echo Python 3.10 or newer is required:  winget install Python.Python.3.12
  pause
  exit /b 1
)
%PYCMD% -m edm_ai demo --lan --no-browser %*
pause
