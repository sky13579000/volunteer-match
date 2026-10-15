@echo off
setlocal
cd /d "%~dp0"
title EDM-AI server (keep this window open)
echo ==================================================
echo  EDM-AI start   folder: %CD%
echo ==================================================
if not exist "edm_ai\__init__.py" goto nofolder
set PYCMD=
py -3 -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1
if not errorlevel 1 set PYCMD=py -3
if defined PYCMD goto havepy
python -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1
if not errorlevel 1 set PYCMD=python
if defined PYCMD goto havepy
goto nopython
:havepy
echo [1/3] Python found: %PYCMD%
%PYCMD% -c "import edm_ai;print('[2/3] edm_ai version',edm_ai.__version__)"
if errorlevel 1 goto importfail
echo [3/3] Starting the server. A browser window opens by itself.
echo       If it does not, open the address printed below that says  http://...
echo       Do NOT close this window. Close it (or press Ctrl+C) to stop the server.
echo.
%PYCMD% -m edm_ai demo --lan
echo.
echo The server stopped. Read the message above, then press any key.
pause
exit /b 0
:nofolder
echo ERROR: the folder "edm_ai" is not next to this file.
echo Move START-HERE.bat into the folder that contains the "edm_ai" folder and run it again.
pause
exit /b 1
:nopython
echo ERROR: Python 3.10 or newer was not found.
echo Install it with this command in PowerShell, then run this file again:
echo     winget install Python.Python.3.12
pause
exit /b 1
:importfail
echo ERROR: Python could not load edm_ai. Take a photo or screenshot of this window and send it.
pause
exit /b 1
