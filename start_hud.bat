@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo Run setup_and_run.bat first.
  pause
  exit /b 1
)
start "" .venv\Scripts\pythonw.exe "%~dp0overlay.py"
