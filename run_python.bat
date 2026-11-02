@echo off
rem Select the same interpreter for dependency installation and execution.
if defined CUMT_PYTHON (
    "%CUMT_PYTHON%" %*
    exit /b
)
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" %*
    exit /b
)
python %*
exit /b
