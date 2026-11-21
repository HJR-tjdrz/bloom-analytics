@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   Step 3/4  Build data warehouse
echo   dim_date / dim_customer(SCD2) / dim_product -> DWD
echo   fact_sales -> DWS -> ADS
echo ============================================================
echo.
"%PY%" run.py dw
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC%
) else (
    echo [OK] Warehouse built.
)
pause
exit /b %RC%
