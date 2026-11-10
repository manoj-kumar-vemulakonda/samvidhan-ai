@echo off
rem ==================================================================
rem  Samvidhan AI - one-time setup. Double-click this file.
rem  It creates a private Python environment (.venv) inside this
rem  folder, installs the packages, and builds the search indexes.
rem ==================================================================
setlocal
cd /d "%~dp0"
title Samvidhan AI - setup

rem Keep downloads and temporary files on this drive, inside .cache
set "PIP_CACHE_DIR=%~dp0.cache\pip"
set "TEMP=%~dp0.cache\tmp"
set "TMP=%~dp0.cache\tmp"
if not exist ".cache\tmp" mkdir ".cache\tmp"

echo.
echo  ============================================
echo   Samvidhan AI - one-time setup
echo  ============================================
echo.

if exist ".venv\Scripts\python.exe" goto install
echo [1/5] Creating the Python environment (.venv) ...
py -3.11 -m venv .venv >nul 2>&1
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv >nul 2>&1
if not exist ".venv\Scripts\python.exe" python -m venv .venv >nul 2>&1
if not exist ".venv\Scripts\python.exe" goto no_python

:install
echo [2/5] Installing packages - this can take a few minutes the first time ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto pip_failed

echo [3/5] Settings file ...
if exist ".env" (
    echo       .env already exists - keeping your settings.
) else (
    copy ".env.example" ".env" >nul
    echo       Created .env - open it with Notepad to change settings.
)

echo [4/5] Building the Constitution search index - first time downloads a small AI model, about 80 MB ...
".venv\Scripts\python.exe" -m app.search --build
if errorlevel 1 goto index_failed

echo [5/5] Building the law library search index (about 100 Acts) - ONE time, about 20 minutes ...
echo       You may close this window and run start.bat instead: the app then finishes it in the background.
".venv\Scripts\python.exe" -m app.laws --build
if errorlevel 1 goto index_failed

echo.
echo  ============================================
echo   Setup finished!  Now double-click start.bat
echo  ============================================
echo.
pause
exit /b 0

:no_python
echo.
echo  ERROR: Python was not found.
echo  Install Python 3.11 or newer from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH" during installation. Then run setup.bat again.
pause
exit /b 1

:pip_failed
echo.
echo  ERROR: Installing packages failed. Check your internet connection
echo  and run setup.bat again. The messages above say what went wrong.
pause
exit /b 1

:index_failed
echo.
echo  ERROR: Building the search index failed. The messages above say why.
echo  The first build needs internet to download the embedding model.
pause
exit /b 1
