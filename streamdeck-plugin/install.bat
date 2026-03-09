@echo off
REM Install the Office Hours Stream Deck plugin (Windows)
setlocal

set "PLUGIN_DIR=com.officehours.intercom.sdPlugin"
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo Installing Office Hours Stream Deck plugin...

REM Install Node dependencies (only if npm available and node_modules missing)
if not exist "%PLUGIN_DIR%\bin\node_modules" (
    where npm >nul 2>&1
    if not errorlevel 1 (
        echo   Installing dependencies...
        cd "%PLUGIN_DIR%\bin"
        call npm install --production
        cd /d "%SCRIPT_DIR%"
    ) else (
        echo   WARNING: npm not found and node_modules not bundled.
        echo   The plugin may not work. Install Node.js from https://nodejs.org
    )
) else (
    echo   Dependencies already bundled.
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
xcopy "%PLUGIN_DIR%" "%DEST%" /E /I /Q /Y >nul

echo.
echo Done! Restart the Stream Deck app, then find "Office Hours" in the action list.
pause
