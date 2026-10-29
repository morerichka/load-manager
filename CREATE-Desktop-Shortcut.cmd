@echo off
setlocal
chcp 65001 >nul
title Load Manager - Desktop shortcut
pushd "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
where python >nul 2>nul
if not errorlevel 1 goto python_direct
echo Install Python 3.12 from https://www.python.org/downloads/windows/
goto failed
:python_launcher
py -3 "scripts\bootstrap.py" --shortcut
goto finished
:python_direct
python "scripts\bootstrap.py" --shortcut
:finished
if errorlevel 1 goto failed
echo.
echo Your Load Manager shortcut is ready on the desktop.
echo Keep this project folder in its current location.
pause
popd
exit /b 0
:failed
echo.
echo Could not create the shortcut. See START-HERE.txt.
pause
popd
exit /b 1
