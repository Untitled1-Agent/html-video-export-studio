@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call install_windows.bat
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" app.py
if errorlevel 1 (
  echo Application failed to start. See the error above.
  pause
  exit /b 1
)
