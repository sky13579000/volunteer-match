@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYCMD=
py -3 -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set PYCMD=py -3
if not defined PYCMD python -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set PYCMD=python
if not defined PYCMD (
  echo.
  echo  Python 3.10 이상이 필요합니다. 이 PC 에 없으면 아래 중 하나로 설치한 뒤 다시 실행하세요.
  echo    winget install Python.Python.3.12
  echo    또는 https://www.python.org/downloads/  ^(설치 첫 화면에서 "Add python.exe to PATH" 체크^)
  echo.
  pause
  exit /b 1
)
%PYCMD% -m edm_ai demo %*
if errorlevel 1 pause
