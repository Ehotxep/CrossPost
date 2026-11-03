@echo off
setlocal
set "PYTHONHOME="
set "PYTHONPATH="
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto setup
if not exist ".venv\.crosspost-ready" goto setup
goto run
:setup
call Install.bat
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" exit /b 1
if not exist ".venv\.crosspost-ready" exit /b 1
:run
".venv\Scripts\python.exe" -m app
pause
