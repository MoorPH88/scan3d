@echo off
rem Drag a folder of photos onto this file to make an STL without the phone app.
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag a folder of photos onto process_folder.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scanstation.py process "%~1"
pause
