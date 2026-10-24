@echo off
rem Mail Adagio launcher: double-click to start.
rem First run: creates a private Python environment (.venv), installs the
rem libraries, then asks for your Gmail details. Nothing is changed in Gmail.
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
if not exist ".venv\installed.txt" (
  "%APP%" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :failed
  echo ok> ".venv\installed.txt"
)

rem --- 3. Menu --------------------------------------------------------------
:menu
cls
echo.
echo   Mail Adagio
echo   -----------------------------------------
echo   1  Preview: sort the 20 newest mails (read-only)
echo   2  Preview: choose how many mails
echo   3  Check the Gmail connection
echo   4  Settings: Gmail, password, model
echo   5  Which local model fits this PC
echo   6  Run the tests
echo   Q  Quit
echo.
set "CHOICE="
set /p "CHOICE=  Your choice: "
if /i "%CHOICE%"=="1" "%APP%" adagio.py preview --limit 20 & goto :pause
if /i "%CHOICE%"=="2" goto :custom
if /i "%CHOICE%"=="3" "%APP%" adagio.py check & goto :pause
if /i "%CHOICE%"=="4" "%APP%" adagio.py setup & goto :pause
if /i "%CHOICE%"=="5" "%APP%" adagio.py hardware & goto :pause
if /i "%CHOICE%"=="6" "%APP%" -m unittest -v & goto :pause
if /i "%CHOICE%"=="Q" exit /b 0
goto :menu

:custom
set "N=20"
set /p "N=  How many mails? [20]: "
"%APP%" adagio.py preview --limit %N%
goto :pause

:pause
echo.
pause
goto :menu

:failed
echo.
echo   Installation failed. Check your internet connection, then try again.
echo   To start over, delete the .venv folder.
echo.
pause
exit /b 1
