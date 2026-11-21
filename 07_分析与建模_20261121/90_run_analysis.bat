@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   Round 3 - Analysis and modeling (8 modules)
echo   Requires: round 1 (warehouse) + round 2 (features)
echo ============================================================
echo.
"%PY%" run.py analysis
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC%
) else (
    echo [OK] Analysis finished. See reports and charts in this folder.
)
pause
exit /b %RC%
