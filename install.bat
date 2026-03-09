@echo off
chcp 65001 >nul 2>&1
REM Office Hours - Windows Installer
REM Double-click to set up everything from scratch.

cd /d "%~dp0"

echo ============================================
echo   Office Hours - Install
echo ============================================
echo.

REM ── Check Python ──
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo Found Python:
python --version
echo.

REM ── Clone repo if needed ──
if not exist "main.py" (
    echo No source files found. Cloning from git...
    git --version >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Git not found.
        echo Please install Git from https://git-scm.com/download/win
        echo.
        pause
        exit /b 1
    )
    git clone https://github.com/matthannam-fart/office-hours.git .
    if errorlevel 1 (
        echo ERROR: Git clone failed.
        pause
        exit /b 1
    )
    echo.
)

REM ── Create virtual environment ──
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo.
)

REM ── Install dependencies ──
echo Installing dependencies...
venv\Scripts\pip install --upgrade pip -q
venv\Scripts\pip install -r requirements.txt -q
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install dependencies.
    echo Check the output above for details.
    pause
    exit /b 1
)

REM ── Install Opus codec library ──
echo.
echo Setting up Opus audio codec...
venv\Scripts\python fetch_opus.py

echo.
echo ============================================
echo   Install complete!
echo   Run start.bat to launch Office Hours.
echo ============================================
echo.
pause
