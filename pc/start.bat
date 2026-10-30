@echo off
title ScanStation
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ScanStation is not installed yet - run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scanstation.py serve
pause
