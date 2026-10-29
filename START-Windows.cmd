@echo off
setlocal
chcp 65001 >nul
title Load Manager - Start
pushd "%~dp0"
echo.
echo   LOAD MANAGER
echo   Preparing your workspace...
echo.
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
where python >nul 2>nul
if not errorlevel 1 goto python_direct
echo Python is not installed. Install Python 3.12 from:
echo https://www.python.org/downloads/windows/
echo Then double-click START-Windows.cmd again.
echo See START-HERE.txt for instructions.
goto failed
:python_launcher
py -3 "scripts\bootstrap.py" --launch
goto finished
:python_direct
python "scripts\bootstrap.py" --launch
:finished
if errorlevel 1 goto failed
popd
exit /b 0
:failed
echo.
echo Load Manager could not start. Read the message above and START-HERE.txt.
pause
popd
exit /b 1
