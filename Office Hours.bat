@echo off
chcp 65001 >nul 2>&1
REM Office Hours - Windows
REM Double-click this file to launch

REM Ensure the window stays open no matter what happens
REM (even on crashes, errors, or early exits)
if "%OH_WRAPPED%"=="" (
    set "OH_WRAPPED=1"
    cmd /k "%~f0" %*
    exit /b
)

cd /d "%~dp0"

echo.
echo   ============================================
echo     Office Hours - Intercom
echo   ============================================
echo.

REM ── Step 0: Auto-update ──
set "REPO_URL=https://github.com/matthannam-fart/office-hours"

git --version >nul 2>&1
if errorlevel 1 goto :no_git

REM Git is available — check if this is a git repo
if not exist ".git" goto :no_git

REM Git user — stash local changes, pull, restore
echo   Checking for updates...
git stash -q >nul 2>&1
git pull --ff-only >nul 2>&1
if errorlevel 1 (
    echo   . Already up to date.
) else (
    echo   . Updated to latest version.
    if exist ".deps_ok" del ".deps_ok" >nul 2>&1
)
git stash pop -q >nul 2>&1
echo.
goto :done_update

:no_git
REM Non-git user — download latest from GitHub via curl + tar
echo   Checking for updates...

REM Get latest commit SHA using PowerShell JSON parsing (more reliable)
set "LATEST_SHA="
for /f "tokens=*" %%a in ('powershell -Command "(Invoke-RestMethod -Uri 'https://api.github.com/repos/matthannam-fart/office-hours/commits/main' -TimeoutSec 5).sha" 2^>nul') do (
    set "LATEST_SHA=%%a"
)

REM Read local version if it exists
set "LOCAL_SHA="
if exist ".version" (
    set /p LOCAL_SHA=<.version
)

REM Compare and update if different
if "%LATEST_SHA%"=="" (
    echo   . Already up to date (no internet?^).
    goto :done_update
)
if "%LATEST_SHA%"=="%LOCAL_SHA%" (
    echo   . Already up to date.
    goto :done_update
)

echo   New version available - downloading...
set "TMPDIR=%TEMP%\oh_update_%RANDOM%"
mkdir "%TMPDIR%" >nul 2>&1
curl -fsSL "%REPO_URL%/archive/refs/heads/main.zip" -o "%TMPDIR%\update.zip" 2>nul
if errorlevel 1 (
    echo   . Could not download update.
    rmdir /s /q "%TMPDIR%" >nul 2>&1
    goto :done_update
)

REM Extract the zip
powershell -Command "Expand-Archive -Force '%TMPDIR%\update.zip' '%TMPDIR%'" >nul 2>&1
if errorlevel 1 (
    echo   . Could not extract update.
    rmdir /s /q "%TMPDIR%" >nul 2>&1
    goto :done_update
)

REM Verify extraction produced the expected directory
if not exist "%TMPDIR%\office-hours-main\main.py" (
    echo   . Could not verify update files.
    rmdir /s /q "%TMPDIR%" >nul 2>&1
    goto :done_update
)

REM Copy updated files over (preserve runtime/generated files)
robocopy "%TMPDIR%\office-hours-main" "%~dp0" /E /XD venv .git __pycache__ /XF .version .last_update_check .deps_ok crash.log >nul 2>&1

REM Robocopy exit codes 0-7 are success, 8+ are errors
if errorlevel 8 (
    echo   . Could not copy update files.
    rmdir /s /q "%TMPDIR%" >nul 2>&1
    goto :done_update
)

REM Save the new version only after successful copy
echo %LATEST_SHA%> .version
echo   . Updated to latest version.
if exist ".deps_ok" del ".deps_ok" >nul 2>&1
rmdir /s /q "%TMPDIR%" >nul 2>&1

:done_update

REM ── Step 1: Check for Python ──
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ============================================
    echo     Python 3 is required but not installed.
    echo   ============================================
    echo.
    echo   Please download and install Python from:
    echo     https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: On the first install screen,
    echo   check the box that says:
    echo     [x] "Add Python to PATH"
    echo.
    echo   Then run this script again.
    echo.
    pause
    goto :eof
)

echo   Using Python:
for /f "tokens=*" %%v in ('python --version') do echo     %%v

REM ── Step 2: Create venv if needed ──
if not exist venv (
    echo.
    echo   Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo.
        echo   ERROR: Failed to create virtual environment.
        echo   Try: python -m pip install --upgrade pip
        pause
        exit /b 1
    )
)

REM ── Step 3: Verify dependencies are installed ──
REM .deps_ok is deleted after updates to force a reinstall
set "NEED_DEPS=0"
if not exist ".deps_ok" set "NEED_DEPS=1"
venv\Scripts\python -c "import sounddevice, numpy, PySide6, zeroconf, cryptography, pynput, opuslib" >nul 2>&1
if errorlevel 1 set "NEED_DEPS=1"
if "%NEED_DEPS%"=="1" (
    echo   Installing dependencies (this may take a minute^)...
    venv\Scripts\pip install --upgrade pip -q
    venv\Scripts\pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   ============================================
        echo     Some dependencies failed to install.
        echo   ============================================
        echo.
        echo   You may need Visual C++ Build Tools:
        echo     https://visualstudio.microsoft.com/visual-cpp-build-tools/
        echo.
        echo   Or try installing Python 3.12 from python.org
        echo   (make sure to check "Add to PATH"^).
        echo.
        pause
        exit /b 1
    )
    echo.> .deps_ok
    echo.
    echo   Dependencies installed.
) else (
    echo   All dependencies OK.
)

REM ── Step 3.5: Ensure Opus codec library is available ──
venv\Scripts\python fetch_opus.py
venv\Scripts\python -c "import opuslib; print('  Opus codec: OK')" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   WARNING: Opus codec not available - audio will use lower quality.
    echo   Try placing opus.dll in: %~dp0
    echo.
)

REM ── Step 3.7: Auto-install Stream Deck plugin ──
set "SD_PLUGIN_DIR=%APPDATA%\Elgato\StreamDeck\Plugins"
set "SD_PLUGIN_NAME=com.officehours.intercom.sdPlugin"
if exist "%SD_PLUGIN_DIR%" (
    if exist "streamdeck-plugin\%SD_PLUGIN_NAME%\manifest.json" (
        REM Validate plugin has bundled node_modules
        if not exist "streamdeck-plugin\%SD_PLUGIN_NAME%\bin\node_modules\ws" (
            echo   Stream Deck plugin missing node_modules...
            where npm >nul 2>&1
            if not errorlevel 1 (
                pushd "streamdeck-plugin\%SD_PLUGIN_NAME%\bin"
                call npm install --production >nul 2>&1
                popd
            ) else (
                echo   . npm not found — plugin may not work.
            )
        )
        echo   Installing Stream Deck plugin...
        if exist "%SD_PLUGIN_DIR%\%SD_PLUGIN_NAME%" rmdir /s /q "%SD_PLUGIN_DIR%\%SD_PLUGIN_NAME%" >nul 2>&1
        xcopy "streamdeck-plugin\%SD_PLUGIN_NAME%" "%SD_PLUGIN_DIR%\%SD_PLUGIN_NAME%" /E /I /Q /Y >nul 2>&1
        if errorlevel 1 (
            echo   . Stream Deck plugin install failed (copy error^).
        ) else (
            echo   . Stream Deck plugin ready.
        )
    )
)

REM ── Step 4: Launch ──
echo.
echo   Starting Office Hours...
echo   ============================================
echo.
echo   NOTE: If this is your first run, Windows Firewall may ask
echo         to allow Python network access. Click "Allow" for
echo         peer discovery to work on your local network.
echo.
venv\Scripts\python run.py
if errorlevel 1 (
    echo.
    echo   ──────────────────────────────────────
    echo   Office Hours exited unexpectedly.
    if exist crash.log (
        echo.
        echo   Crash log:
        type crash.log
    )
    echo.
    echo   If this keeps happening, please report at:
    echo   https://github.com/matthannam-fart/office-hours/issues
    echo   ──────────────────────────────────────
)
echo.
pause
