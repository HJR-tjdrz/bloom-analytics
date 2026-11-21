@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   Step 2/4  Load data into MySQL
echo   - bloom_oltp : clean business tables
echo   - bloom_dw   : ODS raw layer (dirty export)
echo ============================================================
echo.
"%PY%" run.py load
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC%
) else (
    echo [OK] Data loaded.
)
pause
exit /b %RC%
