# Register the ClaudeUsageDashboard task in Windows Task Scheduler.
# Run once as the current user: powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1

$repoDir  = Split-Path $PSScriptRoot
$pythonw  = "$env:USERPROFILE\miniconda3\envs\claude-usage-dashboard\pythonw.exe"

# Launch pythonw.exe directly — it has no console window, so Task Scheduler
# will not open or keep any visible window.
$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument "app.py --port 8080" `
    -WorkingDirectory $repoDir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
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
