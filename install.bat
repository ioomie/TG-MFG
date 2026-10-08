@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python.exe" (
  echo Download the Windows x64 package from the TG-MFG website first.
  pause
  exit /b 1
)
"runtime\python.exe" -B -X utf8 install_core.py
if errorlevel 1 (
  pause
  exit /b 1
)
echo Installed. You can now use the Start Core button on the website.
pause
