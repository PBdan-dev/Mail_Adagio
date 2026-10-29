@echo off
rem Mail Adagio launcher: double-click to start.
rem First run: creates a private Python environment (.venv), installs the
rem libraries, then opens Mail Adagio in your browser. Nothing is changed in Gmail.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title Mail Adagio

rem --- 1. Find Python -------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo.
  echo   Python is not installed. Get it from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" during the install.
  echo.
  pause
  exit /b 1
)

rem --- 2. Private environment and libraries (first run only) ----------------
if not exist ".venv\Scripts\python.exe" (
  echo   Preparing Mail Adagio, first run only...
  %PY% -m venv .venv || goto :failed
)
set "APP=.venv\Scripts\python.exe"
rem Installs again when requirements.txt changes (0.9.0 adds keyring for the Windows vault).
fc /b requirements.txt ".venv\installed.txt" >nul 2>&1 || (
  "%APP%" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :failed
  copy /y requirements.txt ".venv\installed.txt" >nul
)

rem --- 3. Open the interface in your browser, without a console window ----
rem pythonw runs with no window; messages go to adagio-window.log.
rem Mail Adagio stops when you close its tab.
set "APPW=.venv\Scripts\pythonw.exe"
if not exist "%APPW%" set "APPW=%APP%"
start "" "%APPW%" adagio.py ui %*
exit /b 0

:failed
echo.
echo   Installation failed. Check your internet connection, then try again.
echo   To start over, delete the .venv folder.
echo.
pause
exit /b 1
