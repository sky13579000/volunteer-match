@echo off
rem 더블클릭 한 번: 이전 EDM-AI 서버 종료 -> LAN 공개로 시작 -> 접속 주소 표시.  (기존 데이터 유지, 초기화는 start-server.bat --reset)
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'edm_ai' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
rem 방화벽 허용(관리자 권한일 때만 성공, 실패해도 무시)
netsh advfirewall firewall add rule name="EDM-AI" dir=in action=allow protocol=TCP localport=8787-8806 >nul 2>&1
set PYCMD=
py -3 -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set PYCMD=py -3
if not defined PYCMD python -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set PYCMD=python
if not defined PYCMD (
  echo Python 3.10 이상이 필요합니다:  winget install Python.Python.3.12
  pause
  exit /b 1
)
%PYCMD% -m edm_ai demo --lan --no-browser %*
pause
