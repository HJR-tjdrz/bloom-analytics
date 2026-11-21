@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   Round 2 - Step 1/2  ETL: ODS -> DWD clean + reconcile + features
echo   Prerequisite: round 1 finished (python run.py all)
echo ============================================================
echo.
"%PY%" run.py etl
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC%
) else (
    echo [OK] ETL finished. Next: 05_...\90_run_governance.bat
)
pause
exit /b %RC%
