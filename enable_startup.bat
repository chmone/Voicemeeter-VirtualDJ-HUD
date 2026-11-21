@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup_and_run.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe startup.py
pause
