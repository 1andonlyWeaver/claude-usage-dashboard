# Register the ClaudeUsageDashboard task in Windows Task Scheduler.
# Run once as the current user: powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1

$repoDir  = Split-Path $PSScriptRoot
$pythonw  = "$env:USERPROFILE\miniconda3\envs\claude-usage-dashboard\pythonw.exe"
$launcher = "$repoDir\scripts\launcher.py"

# Run via pythonw.exe (no console window) through a launcher script that checks
# whether port 8080 is already in use before starting, preventing silent failures
# when the server is already running.
$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument $launcher `
    -WorkingDirectory $repoDir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "ClaudeUsageDashboard" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Limited `
    -Force

Write-Host "Task registered. The dashboard will start automatically at next login."
Write-Host "To start it now without logging out, run: Start-ScheduledTask -TaskName ClaudeUsageDashboard"
