@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0libs;%PYTHONPATH%"
call "%~dp0run_python.bat" browser_login.py
echo.
echo === 按任意键关闭窗口 ===
pause
