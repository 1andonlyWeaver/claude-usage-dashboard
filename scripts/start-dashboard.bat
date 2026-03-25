@echo off
:: Start the Claude usage dashboard server.
:: Intended to be run via Windows Task Scheduler at login.
:: Logs are written to logs\dashboard.log in the repo directory.

set REPO_DIR=%~dp0..
set PYTHON=%USERPROFILE%\miniconda3\envs\claude-usage-dashboard\python.exe
set LOG_DIR=%REPO_DIR%\logs
set LOG_FILE=%LOG_DIR%\dashboard.log

:: Ensure log directory exists
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Skip startup if server is already listening on port 8080
netstat -ano | findstr ":8080 " | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo [%DATE% %TIME%] Server already running on port 8080, skipping. >> "%LOG_FILE%"
    exit /b 0
)

echo [%DATE% %TIME%] Starting dashboard server... >> "%LOG_FILE%"
cd /d "%REPO_DIR%"
"%PYTHON%" app.py --port 8080 >> "%LOG_FILE%" 2>&1
echo [%DATE% %TIME%] Server exited with code %ERRORLEVEL%. >> "%LOG_FILE%"
