@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   BloomAnalytics - Full pipeline (gen -> load -> dw -> check)
echo   Prerequisite: MySQL initialized by folder 04 script.
echo ============================================================
echo.
"%PY%" run.py all
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC%
) else (
    echo [OK] Full pipeline finished.
)
pause
exit /b %RC%
