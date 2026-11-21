@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   Step 1/4  Generate simulated business data
echo   Output: 03_数据工厂_20261120\data  (~10w orders)
echo ============================================================
echo.
"%PY%" run.py gen
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC%
) else (
    echo [OK] Data generated.
)
pause
exit /b %RC%
