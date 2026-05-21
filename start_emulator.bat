@echo off
cd /d "%~dp0"
python ray5_emulator.py
if errorlevel 1 (
  echo.
  echo Emulator exited with error.
  pause
)
