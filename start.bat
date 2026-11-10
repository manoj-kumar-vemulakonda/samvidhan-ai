@echo off
rem ==================================================================
rem  Samvidhan AI - start the app. Double-click this file.
rem  Your browser opens at http://127.0.0.1:8000
rem  To stop the app: close this window (or press Ctrl+C in it).
rem ==================================================================
setlocal
cd /d "%~dp0"
title Samvidhan AI

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  Setup has not been run yet. Double-click setup.bat first.
    echo.
    pause
    exit /b 1
)

rem Ask the app to open the browser once it is ready
set "OPEN_BROWSER=1"

echo.
echo  Starting Samvidhan AI at http://127.0.0.1:8000
echo  Keep this window open while you use the app. Close it to stop.
echo.
rem 127.0.0.1 = only this computer can open the app (protects your API credits)
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo  The app has stopped.
pause
