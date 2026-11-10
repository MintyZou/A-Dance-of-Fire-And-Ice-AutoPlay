@echo off
cd /d "%~dp0"
where pythonw >nul 2>&1
if errorlevel 1 (
  echo Python not found in PATH.
  echo Use dist\ADOFAI_AutoPlay.exe instead, or run run_debug.bat to see the error.
  pause
  exit /b 1
)
start "" pythonw app.py
