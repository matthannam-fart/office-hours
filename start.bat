@echo off
chcp 65001 >nul 2>&1
REM Office Hours - Windows Launcher
REM Double-click to start Office Hours.

cd /d "%~dp0"

echo ============================================
echo   Office Hours
echo ============================================
echo.

REM ── Check venv exists ──
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment not found.
    echo Please run install.bat first.
    echo.
    pause
    exit /b 1
)

REM ── Launch ──
venv\Scripts\python run.py

if errorlevel 1 (
    echo.
    echo Office Hours exited with an error.
    echo Check crash.log for details.
    echo.
    pause
)
