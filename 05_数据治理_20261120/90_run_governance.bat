@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   Round 2 - Step 2/2  Governance: quality / dictionary / metrics / tags
echo   Prerequisite: 06_ETL...\90_run_etl.bat finished
echo ============================================================
echo.
"%PY%" run.py gov
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC%
) else (
    echo [OK] Governance reports written to this folder.
)
pause
exit /b %RC%
