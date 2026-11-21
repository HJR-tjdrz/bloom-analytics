@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   BloomAnalytics Dashboard
echo   http://127.0.0.1:5050
echo   Requires: round 1-3 finished (warehouse + analysis tables)
echo ============================================================
echo.

start "BloomAnalytics Dashboard" cmd /k "%PY%" run.py dashboard --port 5050
timeout /t 5 >nul
start "" http://127.0.0.1:5050
