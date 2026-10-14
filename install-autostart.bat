@echo off
rem Start EDM-AI automatically at Windows logon (undo: uninstall-autostart.bat)
cd /d "%~dp0"
schtasks /create /tn "EDM-AI" /tr "cmd /c start \"EDM-AI\" /min \"%~dp0start-server.bat\"" /sc onlogon /rl limited /f
if errorlevel 1 (echo Failed. Alternative: put a shortcut to start-server.bat in the shell:startup folder.) else (echo Done. The server starts automatically at the next logon.)
pause
