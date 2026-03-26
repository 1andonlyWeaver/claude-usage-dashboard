@echo off
:: Start the Claude usage dashboard server in the background (no visible window).
:: Intended to be run via Windows Task Scheduler at login.
:: Logs are written to logs\dashboard.log in the repo directory.

set REPO_DIR=%~dp0..
set PYTHONW=%USERPROFILE%\miniconda3\envs\claude-usage-dashboard\pythonw.exe
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

echo [%DATE% %TIME%] Starting dashboard server (detached)... >> "%LOG_FILE%"
cd /d "%REPO_DIR%"
start "" /b "%PYTHONW%" app.py --port 8080
echo [%DATE% %TIME%] Dashboard process launched. >> "%LOG_FILE%"
