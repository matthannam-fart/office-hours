@echo off
REM Install the Office Hours Stream Deck plugin (Windows)
setlocal

set "PLUGIN_DIR=com.officehours.intercom.sdPlugin"
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo Installing Office Hours Stream Deck plugin...

REM Install Node dependencies
echo   Installing dependencies...
cd "%PLUGIN_DIR%\bin"
call npm install --production
cd /d "%SCRIPT_DIR%"

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
