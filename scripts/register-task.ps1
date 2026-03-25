# Register the ClaudeUsageDashboard task in Windows Task Scheduler.
# Run once as the current user: powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1

$scriptPath = Join-Path $PSScriptRoot "start-dashboard.bat"

$action = New-ScheduledTaskAction -Execute $scriptPath

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "ClaudeUsageDashboard" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Limited `
    -Force

Write-Host "Task registered. The dashboard will start automatically at next login."
Write-Host "To start it now without logging out, run: Start-ScheduledTask -TaskName ClaudeUsageDashboard"
