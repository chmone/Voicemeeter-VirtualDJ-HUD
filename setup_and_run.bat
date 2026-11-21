@echo off
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  echo Python 3.10+ is required. Install Python from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 (pause & exit /b 1)
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (pause & exit /b 1)
start "" .venv\Scripts\pythonw.exe "%~dp0overlay.py"
echo Started HUD. Look for its icon in the system tray.
timeout /t 3 >nul
