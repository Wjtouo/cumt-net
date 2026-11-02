@echo off
cd /d "%~dp0"
call "%~dp0run_python.bat" cumt_net.py --status
echo.
pause
