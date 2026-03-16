@echo off
REM Install the Vox Stream Deck plugin (Windows)
setlocal

set "PLUGIN_DIR=com.vox.intercom.sdPlugin"
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo Installing Vox Stream Deck plugin...

REM Validate Node dependencies (ws module must exist)
if not exist "%PLUGIN_DIR%\bin\node_modules\ws" (
    echo   Node dependencies missing or incomplete.
    where npm >nul 2>&1
    if not errorlevel 1 (
        echo   Installing dependencies via npm...
        cd "%PLUGIN_DIR%\bin"
        call npm install --production
        cd /d "%SCRIPT_DIR%"
        if not exist "%PLUGIN_DIR%\bin\node_modules\ws" (
            echo   WARNING: npm install succeeded but ws module still missing.
            echo   The plugin may not work.
        ) else (
            echo   Dependencies installed.
        )
    ) else (
        echo   WARNING: npm not found and ws module not bundled.
        echo   The plugin may not work. Install Node.js from https://nodejs.org
    )
) else (
    echo   Dependencies OK.
)

REM Determine install location
set "DEST=%APPDATA%\Elgato\StreamDeck\Plugins\%PLUGIN_DIR%"

REM Remove old version
if exist "%DEST%" (
    echo   Removing old version...
    rmdir /s /q "%DEST%"
)

REM Copy plugin
echo   Copying plugin to Stream Deck...
xcopy "%PLUGIN_DIR%" "%DEST%" /E /I /Q /Y >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: Failed to copy plugin to Stream Deck plugins directory.
    echo   Check that the directory is writable: %DEST%
) else (
    echo.
    echo Done! Restart the Stream Deck app, then find "Vox" in the action list.
)
pause
