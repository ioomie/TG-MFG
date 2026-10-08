@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto failed

rem The portable Windows package includes the official Python runtime.
if exist "runtime\python.exe" goto portable
if exist ".venv\Scripts\python.exe" goto dependencies

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto dependencies

:try_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 goto failed

:dependencies
".venv\Scripts\python.exe" -c "from importlib.metadata import version; assert all(version(n) == v for n, v in [('python-socks', '3.1.1'), ('async-timeout', '5.0.1'), ('Telethon', '1.45.0'), ('PySocks', '1.7.1'), ('pyaes', '1.6.1'), ('rsa', '4.9.1'), ('pyasn1', '0.6.4')])" >nul 2>&1
if not errorlevel 1 goto launch
echo Installing verified dependencies from PyPI. Internet access is needed once.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --index-url https://pypi.org/simple --require-hashes --timeout 15 --retries 1 -r requirements.txt
if errorlevel 1 goto failed

:launch
".venv\Scripts\python.exe" -B -X utf8 app.py --open-browser %*
if errorlevel 1 goto failed
exit /b 0

:portable
"runtime\python.exe" -B -X utf8 app.py --open-browser %*
if errorlevel 1 goto failed
exit /b 0

:missing_python
echo Python 3.9 or newer was not found.
echo Use the TG-MFG-Windows-x64 portable package, or install Python from:
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:failed
echo.
echo TG-MFG could not start. See the error above and README-Windows.md.
pause
exit /b 1
