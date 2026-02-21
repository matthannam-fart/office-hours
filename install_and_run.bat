@echo off
chcp 65001 >nul 2>&1
REM Office Hours - Windows Install and Run
REM Double-click this file to launch

cd /d "%~dp0"

echo ============================================
echo   Office Hours - Intercom
echo ============================================
echo.

REM Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3 is required but not installed.
    echo Download from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install!
    echo.
    pause
    exit /b 1
)

echo Using Python:
python --version

REM Create venv if needed
if not exist venv (
    echo.
    echo Creating virtual environment...
    python -m venv venv
)

REM Verify dependencies are installed (catches broken/incomplete venvs)
venv\Scripts\python -c "import sounddevice, numpy, PySide6, zeroconf" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    venv\Scripts\pip install --upgrade pip -q
    venv\Scripts\pip install -r requirements.txt
    echo.
    echo Setup complete!
) else (
    echo All dependencies OK.
)

echo.
echo Starting Office Hours...
echo ============================================
echo.
venv\Scripts\python main.py
pause
