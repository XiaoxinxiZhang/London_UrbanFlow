@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not defined AIRWATCH_PYTHON set "AIRWATCH_PYTHON=D:\anaconda3\envs\gpu\python.exe"
if not exist "%AIRWATCH_PYTHON%" set "AIRWATCH_PYTHON=python"
"%AIRWATCH_PYTHON%" -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1
if errorlevel 1 (
 echo Python 3.10+ not found. Set AIRWATCH_PYTHON to your python.exe path.
 pause
 exit /b 1
)
"%AIRWATCH_PYTHON%" tools\run_tests.py
pause
