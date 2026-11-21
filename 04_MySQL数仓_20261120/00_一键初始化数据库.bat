@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
pushd "%~dp0"

if exist "..\.venv\Scripts\python.exe" (set "PY=..\.venv\Scripts\python.exe") else (set "PY=python")

echo ============================================================
echo   Step 0/4  Init MySQL: databases, app account, tables
echo   You will be asked for the MySQL root password once.
echo   The app account password is random and stored in .env only.
echo ============================================================
echo.
"%PY%" "%~dp000_init_database.py"
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code %RC%
) else (
    echo [OK] Database schema is ready.
)
popd
pause
exit /b %RC%
