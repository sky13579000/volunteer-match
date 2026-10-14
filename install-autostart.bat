@echo off
rem Windows 로그인 시 EDM-AI 서버를 자동 시작하도록 등록합니다. (해제: uninstall-autostart.bat)
chcp 65001 >nul
cd /d "%~dp0"
schtasks /create /tn "EDM-AI" /tr "cmd /c start \"EDM-AI\" /min \"%~dp0start-server.bat\"" /sc onlogon /rl limited /f
if errorlevel 1 (echo 등록 실패 - 파일 탐색기에서 shell:startup 폴더에 start-server.bat 바로가기를 넣어도 됩니다.) else (echo 등록 완료: 다음 로그인부터 자동 시작됩니다.)
echo 방화벽: 관리자 권한 PowerShell 에서  netsh advfirewall firewall add rule name="EDM-AI" dir=in action=allow protocol=TCP localport=8787-8806
pause
