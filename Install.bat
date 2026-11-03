@echo off
setlocal
set "PYTHONHOME="
set "PYTHONPATH="
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\install.py
  goto finish
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 tools\install.py
  goto finish
)
where python >nul 2>nul
if not errorlevel 1 (
  python tools\install.py
  goto finish
)
echo Python 3.11 or newer is required. Install it from python.org.
pause
exit /b 1
:finish
set "INSTALL_RESULT=%ERRORLEVEL%"
pause
exit /b %INSTALL_RESULT%
