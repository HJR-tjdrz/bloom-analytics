@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo ============================================================
echo   BloomAnalytics - Install Python Dependencies
echo   (venv: .venv, mirror: aliyun)
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Please install Python 3.9+ and add it to PATH.
        pause
        exit /b 1
    )
) else (
    echo [1/2] Virtual environment already exists, skip.
)

echo [2/2] Installing requirements ...
".venv\Scripts\python.exe" -m pip install --no-input --disable-pip-version-check ^
    -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com ^
    -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] pip install failed. Check your network or try another mirror.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Done. Next: run the database init script in folder 04
echo ============================================================
pause
