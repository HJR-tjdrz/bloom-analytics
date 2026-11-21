@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   BloomAnalytics - Unit tests (pytest)
echo   DB-dependent tests auto-skip when MySQL is unavailable.
echo ============================================================
echo.
"%PY%" -m pytest "11_测试_20261121" -q --tb=short
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] some tests failed, exit code %RC%
) else (
    echo [OK] all tests passed.
)
pause
exit /b %RC%
