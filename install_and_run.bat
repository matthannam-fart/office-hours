@echo off
chcp 65001 >nul 2>&1
REM Office Hours - Windows Install and Run
REM Double-click this file to launch

cd /d "%~dp0"

echo ============================================
echo   Office Hours - Intercom
echo ============================================
echo.

REM ── Step 0: Auto-update ──
set "REPO_URL=https://github.com/matthannam-fart/office-hours"

git --version >nul 2>&1
if errorlevel 1 goto :no_git

REM Git is available — check if this is a git repo
if not exist ".git" goto :no_git

REM Git user — just pull
echo Checking for updates...
git pull --ff-only >nul 2>&1
if errorlevel 1 (
    echo . Already up to date (or merge needed^).
) else (
    echo . Updated to latest version.
)
echo.
goto :done_update

:no_git
REM Non-git user — download latest from GitHub via curl + tar
echo Checking for updates...

REM Get latest commit SHA from GitHub API
set "LATEST_SHA="
for /f "tokens=2 delims=:, " %%a in ('curl -fsSL "https://api.github.com/repos/matthannam-fart/office-hours/commits/main" 2^>nul ^| findstr /n "sha" ^| findstr "^2:"') do (
    set "LATEST_SHA=%%~a"
)

REM Read local version if it exists
set "LOCAL_SHA="
if exist ".version" (
    set /p LOCAL_SHA=<.version
)

REM Compare and update if different
if "%LATEST_SHA%"=="" (
    echo . Could not check for updates (no internet?^).
    goto :done_update
)
if "%LATEST_SHA%"=="%LOCAL_SHA%" (
    echo . Already up to date.
    goto :done_update
)

echo New version available - downloading...
set "TMPDIR=%TEMP%\oh_update_%RANDOM%"
mkdir "%TMPDIR%" >nul 2>&1
curl -fsSL "%REPO_URL%/archive/refs/heads/main.zip" -o "%TMPDIR%\update.zip" 2>nul
if errorlevel 1 (
    echo . Could not download update (no internet?^).
    rmdir /s /q "%TMPDIR%" >nul 2>&1
    goto :done_update
)

REM Extract the zip
powershell -Command "Expand-Archive -Force '%TMPDIR%\update.zip' '%TMPDIR%'" >nul 2>&1
if errorlevel 1 (
    echo . Could not extract update.
    rmdir /s /q "%TMPDIR%" >nul 2>&1
    goto :done_update
)

REM Copy updated files over (preserve venv, .version, user settings)
robocopy "%TMPDIR%\office-hours-main" "%~dp0" /E /XD venv .git __pycache__ /XF .version >nul 2>&1

REM Save the new version
echo %LATEST_SHA%> .version
echo . Updated to latest version.
rmdir /s /q "%TMPDIR%" >nul 2>&1

:done_update

REM ── Step 1: Check for Python ──
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

REM ── Step 2: Create venv if needed ──
if not exist venv (
    echo.
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        echo Try: python -m pip install --upgrade pip
        pause
        exit /b 1
    )
)

REM ── Step 3: Verify dependencies are installed ──
venv\Scripts\python -c "import sounddevice, numpy, PySide6, zeroconf, cryptography, pynput" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    venv\Scripts\pip install --upgrade pip -q
    venv\Scripts\pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to install some dependencies.
        echo You may need to install Visual C++ Build Tools for some packages.
        echo Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/
        pause
        exit /b 1
    )
    echo.
    echo Setup complete!
) else (
    echo All dependencies OK.
)

REM ── Step 4: Launch ──
echo.
echo Starting Office Hours...
echo ============================================
echo.
echo NOTE: If this is your first run, Windows Firewall may ask
echo       to allow Python network access. Click "Allow" for
echo       peer discovery to work on your local network.
echo.
venv\Scripts\python main.py
if errorlevel 1 (
    echo.
    echo Office Hours exited with an error.
    echo Check the output above for details.
)
pause
