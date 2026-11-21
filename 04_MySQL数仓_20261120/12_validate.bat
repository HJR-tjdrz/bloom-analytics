@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   Step 4/4  Data validation and reconciliation
echo   Row counts / amount reconciliation / dimension coverage
echo ============================================================
echo.
"%PY%" run.py check
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [WARN] Some checks failed, exit code %RC%
) else (
    echo [OK] All checks passed.
)
pause
exit /b %RC%
