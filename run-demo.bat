@echo off
cd /d "%~dp0"
set PYCMD=
py -3 -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set PYCMD=py -3
if not defined PYCMD python -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1 && set PYCMD=python
if not defined PYCMD (
  echo Python 3.10 or newer is required:  winget install Python.Python.3.12
  pause
  exit /b 1
)
%PYCMD% -m edm_ai demo %*
if errorlevel 1 pause
