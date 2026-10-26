[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskName = 'DetourVpn Beta Backup Daily'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pullScript = Join-Path $PSScriptRoot 'pull_beta_backup.ps1'
$shellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $shellPath -PathType Leaf)) {
    throw 'Windows PowerShell is unavailable for the scheduled task.'
}
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$actionArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $pullScript
$action = New-ScheduledTaskAction -Execute $shellPath -Argument $actionArguments -WorkingDirectory $repoRoot
if ($existing) {
    $existingAction = $existing.Actions | Select-Object -First 1
    if ($existingAction.Execute -eq $shellPath -and $existingAction.Arguments -eq $actionArguments) {
        Write-Host 'Daily beta backup task is already registered with the expected action.'
        exit 0
    }
    if ($existingAction.Arguments -ne $actionArguments) {
        throw 'A different task with the beta backup task name already exists; it was not changed.'
    }
    Set-ScheduledTask -TaskName $taskName -Action $action | Out-Null
    Write-Host 'Daily beta backup task now uses built-in Windows PowerShell.'
    exit 0
}

$trigger = New-ScheduledTaskTrigger -Daily -At '7:00AM'
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description `
    'Pull and verify the latest encrypted DetourVpn closed-beta database backup into the local user profile.' |
    Out-Null
Write-Host 'Daily beta backup task registered for 07:00 local time; it runs when this user is logged in.'
