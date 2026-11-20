@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
if defined AIRWATCH_PYTHON if exist "%AIRWATCH_PYTHON%" set "PY=%AIRWATCH_PYTHON%"
if not defined PY if exist "D:\anaconda3\envs\gpu\python.exe" set "PY=D:\anaconda3\envs\gpu\python.exe"
if not defined PY for %%P in (python.exe) do set "PY=%%~$PATH:P"
if not defined PY (
  echo Python not found. Edit this bat to set PY to your python.exe path.
  pause
  exit /b 1
)
echo Project: %CD%
"%PY%" -X utf8 "tools\diagnose_data.py"
echo.
pause
