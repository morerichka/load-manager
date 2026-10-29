@echo off
setlocal
chcp 65001 >nul
title Load Manager - Claude kit
pushd "%~dp0"
echo.
echo   LOAD MANAGER - CLAUDE KIT
echo   Installing the Claude hook and skill...
echo.
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
where python >nul 2>nul
if not errorlevel 1 goto python_direct
echo Python is not installed. Install Python 3.12 from:
echo https://www.python.org/downloads/windows/
echo Then double-click INSTALL-Claude-Kit.cmd again.
goto failed
:python_launcher
py -3 "scripts\bootstrap.py" --claude-kit
goto finished
:python_direct
python "scripts\bootstrap.py" --claude-kit
:finished
if errorlevel 1 goto failed
echo.
echo Done. Restart Claude. Then ask it to run: lm doctor
pause
popd
exit /b 0
:failed
echo.
echo Could not install. Read the message above and START-HERE.txt.
pause
popd
exit /b 1
