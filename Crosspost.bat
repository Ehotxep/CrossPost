@echo off
cd /d "%~dp0"
where pyw >nul 2>nul && (
  start "" pyw -3 -m app
  exit /b
)
where pythonw >nul 2>nul && (
  start "" pythonw -m app
  exit /b
)
python -m app
pause
