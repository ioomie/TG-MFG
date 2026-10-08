@echo off
setlocal
if exist "%LOCALAPPDATA%\TG-MFG\installation.json" (
  cd /d "%LOCALAPPDATA%\TG-MFG"
) else (
  cd /d "%~dp0"
)
if not exist "runtime\python.exe" (
  echo Please download the Windows package and run install.bat first.
  pause
  exit /b 1
)
if not exist "debug_core.py" (
  echo Installed core is too old. Run install.bat from the latest download first.
  pause
  exit /b 1
)
"runtime\python.exe" -u -B -X utf8 debug_core.py
pause
