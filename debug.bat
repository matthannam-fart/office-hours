@echo off
chcp 65001 >nul 2>&1
REM Vox - Debug Launcher
REM Double-click to update from git and run with crash logging.

cd /d "%~dp0"

echo ============================================
echo   Vox - Debug Mode
echo ============================================
echo.

REM ── Update from git ──
git --version >nul 2>&1
if errorlevel 1 (
    echo Git not found - skipping update.
    goto :run
)
if not exist ".git" (
    echo Not a git repo - skipping update.
    goto :run
)

echo Pulling latest from git...
git pull --ff-only
echo.

REM ── Reinstall deps if needed ──
if exist venv (
    venv\Scripts\pip install -r requirements.txt -q
    venv\Scripts\python fetch_opus.py
)

:run
REM ── Launch with crash logging ──
echo Starting Vox (debug mode)...
echo ============================================
echo.

if not exist venv (
    echo   No venv found. Run "Vox.bat" first.
    pause
    exit /b 1
)
venv\Scripts\python run.py

echo.
echo ============================================
echo   Vox has exited.
echo   Check crash.log if there was an error.
echo ============================================
echo.
pause
