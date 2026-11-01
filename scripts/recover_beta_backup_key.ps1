[CmdletBinding()]
param([switch]$StartScheduledTask)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$betaHost = '193.68.88.239'
$remote = "root@$betaHost"
$remoteKeyPath = '/etc/detourvpn-beta/backup-secrets/backup-key'
$remoteArchivePath = '/var/lib/detourvpn-beta-backups/latest.enc'
$maximumArchiveBytes = 64MB
$taskName = 'DetourVpn Beta Backup Daily'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$importScript = Join-Path $PSScriptRoot 'import_beta_backup.py'
$pullScript = (Resolve-Path (Join-Path $PSScriptRoot 'pull_beta_backup.ps1')).Path
$windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw 'The current user Local AppData path is unavailable.' }
if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) { throw 'The current user profile path is unavailable.' }
$identityPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$secretParent = Join-Path $env:LOCALAPPDATA 'DetourVpn'
$secretRoot = Join-Path $secretParent 'beta-backup'
$keyPath = Join-Path $secretRoot 'backup.key'
$archiveDirectory = Join-Path $secretRoot 'archives'

foreach ($requiredPath in @($identityPath, $pythonPath, $importScript, $windowsPowerShell)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw 'A required local beta backup recovery file is missing.'
    }
}

$existingKey = Get-Item -LiteralPath $keyPath -Force -ErrorAction SilentlyContinue
if ($existingKey) { throw 'Local beta backup key already exists; recovery will not overwrite it.' }

foreach ($directoryPath in @($secretParent, $secretRoot, $archiveDirectory)) {
    if (Test-Path -LiteralPath $directoryPath) {
        $directory = Get-Item -LiteralPath $directoryPath -Force
        if (-not $directory.PSIsContainer) { throw 'A local beta backup path exists but is not a directory.' }
        if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'A local beta backup path is a reparse point.'
        }
    }
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($StartScheduledTask -and -not $task) { throw 'The expected limited-user beta backup task is not registered.' }
if ($StartScheduledTask) {
    $taskAction = $task.Actions | Select-Object -First 1
    $expectedActionArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $pullScript
    $taskSid = [string]$task.Principal.UserId
    if ($taskSid -notmatch '^S-1-') {
        try {
            $taskSid = ([System.Security.Principal.NTAccount]$taskSid).Translate(
                [System.Security.Principal.SecurityIdentifier]).Value
        } catch {
            throw 'The registered backup task principal could not be verified.'
        }
    }
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if (@($task.Actions).Count -ne 1 -or $taskSid -ne $currentSid -or
        [string]$task.State -eq 'Running' -or
        $task.Principal.LogonType -ne 'Interactive' -or $task.Principal.RunLevel -ne 'Limited' -or
        $taskAction.Execute -ne $windowsPowerShell -or $taskAction.Arguments -ne $expectedActionArguments -or
        $taskAction.WorkingDirectory -ne $repoRoot) {
        throw 'The scheduled task is not configured with the expected limited-user backup action.'
    }
}

$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$scpPath = (Get-Command scp.exe -ErrorAction Stop).Source
$sshOptions = @(
    '-o', 'BatchMode=yes',
    '-o', 'ConnectTimeout=15',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', 'IdentitiesOnly=yes',
    '-i', $identityPath
)

$remotePreflight = @'
set -eu
key_dir=/etc/detourvpn-beta/backup-secrets
archive_dir=/var/lib/detourvpn-beta-backups
key=/etc/detourvpn-beta/backup-secrets/backup-key
archive=/var/lib/detourvpn-beta-backups/latest.enc
test -d "$key_dir" && test ! -L "$key_dir"
test "$(stat -c '%U:%G:%a' -- "$key_dir")" = 'root:detourvpn-beta:750'
test -f "$key" && test ! -L "$key"
test "$(stat -c '%U:%G:%a:%s' -- "$key")" = 'root:detourvpn-beta:440:44'
test -d "$archive_dir" && test ! -L "$archive_dir"
test "$(stat -c '%U:%G:%a' -- "$archive_dir")" = 'detourvpn-beta:detourvpn-beta:700'
test -f "$archive" && test ! -L "$archive"
archive_size=$(stat -c '%s' -- "$archive")
case "$archive_size" in ''|*[!0-9]*) exit 1 ;; esac
test "$archive_size" -gt 0 && test "$archive_size" -le 67108864
echo beta_backup_recovery_preflight_passed
echo "archive_bytes=$archive_size"
'@ -replace [char]13, ''

Write-Host 'Checking existing beta key and encrypted archive metadata over pinned SSH.'
$preflightOutput = @(& $sshPath @sshOptions $remote $remotePreflight)
if ($LASTEXITCODE -ne 0 -or $preflightOutput -notcontains 'beta_backup_recovery_preflight_passed') {
    throw 'Existing beta backup key preflight failed; no key was copied.'
}
$archiveSizeLine = $preflightOutput | Where-Object { $_ -match '^archive_bytes=\d+$' } | Select-Object -First 1
if (-not $archiveSizeLine) { throw 'Remote beta archive size was not validated.' }
$remoteArchiveBytes = [long]($archiveSizeLine -replace '^archive_bytes=', '')

if (-not (Test-Path -LiteralPath $secretRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null
}
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$userDirectoryAcl = '*{0}:(OI)(CI)F' -f $userSid
$systemDirectoryAcl = '*S-1-5-18:(OI)(CI)F'
$userFileAcl = '*{0}:F' -f $userSid
$systemFileAcl = '*S-1-5-18:F'

function Set-RestrictedDirectoryAcl([string]$Path) {
    & icacls.exe $Path /inheritance:r /grant:r $userDirectoryAcl $systemDirectoryAcl | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict local beta backup directory access.' }
}

function Set-RestrictedFileAcl([string]$Path) {
    & icacls.exe $Path /inheritance:r /grant:r $userFileAcl $systemFileAcl | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict local beta backup key access.' }
}

Set-RestrictedDirectoryAcl $secretRoot
if (Test-Path -LiteralPath $archiveDirectory -PathType Container) {
    Set-RestrictedDirectoryAcl $archiveDirectory
}

$recoveryId = [guid]::NewGuid().ToString('N')
$workDirectory = Join-Path $secretRoot ".recovery-$recoveryId"
$stagedKeyPath = Join-Path $workDirectory 'backup-key.part'
$stagedArchivePath = Join-Path $workDirectory 'latest.enc'
$verifiedDirectory = Join-Path $workDirectory 'verified'
$verifiedArchivePath = $null
$keyInstalled = $false
$scheduledResult = $null
$newLocalArchiveVerified = $false

try {
    New-Item -ItemType Directory -Path $workDirectory -Force | Out-Null
    Set-RestrictedDirectoryAcl $workDirectory

    $remoteKey = "${remote}:$remoteKeyPath"
    $remoteArchive = "${remote}:$remoteArchivePath"
    & $scpPath @sshOptions $remoteKey $stagedKeyPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not retrieve the existing beta backup key over SSH.' }
    & $scpPath @sshOptions $remoteArchive $stagedArchivePath
    if ($LASTEXITCODE -ne 0) { throw 'Could not retrieve the existing encrypted beta archive over SSH.' }

    foreach ($stagedPath in @($stagedKeyPath, $stagedArchivePath)) {
        if (-not (Test-Path -LiteralPath $stagedPath -PathType Leaf)) {
            throw 'A staged beta recovery file is missing.'
        }
        if ((Get-Item -LiteralPath $stagedPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'A staged beta recovery file is a reparse point.'
        }
        Set-RestrictedFileAcl $stagedPath
    }
    if ((Get-Item -LiteralPath $stagedKeyPath).Length -ne 44) {
        throw 'The retrieved beta backup key has an unexpected size.'
    }
    if ((Get-Item -LiteralPath $stagedArchivePath).Length -ne $remoteArchiveBytes -or
        (Get-Item -LiteralPath $stagedArchivePath).Length -gt $maximumArchiveBytes) {
        throw 'The retrieved beta archive size does not match the remote metadata.'
    }

    New-Item -ItemType Directory -Path $verifiedDirectory -Force | Out-Null
    Set-RestrictedDirectoryAcl $verifiedDirectory
    $importOutput = @(& $pythonPath $importScript --source $stagedArchivePath `
        --destination-dir $verifiedDirectory --key-file $stagedKeyPath)
    if ($LASTEXITCODE -ne 0) { throw 'The recovered key did not authenticate the existing beta archive.' }
    $importResult = ($importOutput -join [Environment]::NewLine) | ConvertFrom-Json
    if ($importResult.code -ne 'backup_imported' -or [string]::IsNullOrWhiteSpace($importResult.archive)) {
        throw 'The existing beta archive validation did not return the expected result.'
    }
    $verifiedArchivePath = Join-Path $verifiedDirectory $importResult.archive
    if (-not (Test-Path -LiteralPath $verifiedArchivePath -PathType Leaf)) {
        throw 'The existing beta archive validation output is missing.'
    }

    if (Get-Item -LiteralPath $keyPath -Force -ErrorAction SilentlyContinue) {
        throw 'A local beta backup key appeared during recovery; it was not overwritten.'
    }
    $stagedKeyHash = (Get-FileHash -LiteralPath $stagedKeyPath -Algorithm SHA256).Hash
    [IO.File]::Move($stagedKeyPath, $keyPath)
    Set-RestrictedFileAcl $keyPath
    $installedKeyHash = (Get-FileHash -LiteralPath $keyPath -Algorithm SHA256).Hash
    if ($stagedKeyHash -ne $installedKeyHash) { throw 'Installed beta backup key verification failed.' }
    $keyInstalled = $true

    if ($StartScheduledTask) {
        $previousRun = (Get-ScheduledTaskInfo -TaskName $taskName).LastRunTime
        $previousArchiveNames = @()
        if (Test-Path -LiteralPath $archiveDirectory -PathType Container) {
            $previousArchiveNames = @(Get-ChildItem -LiteralPath $archiveDirectory -File -Filter '*.enc' |
                Select-Object -ExpandProperty Name)
        }
        $taskStartedAt = Get-Date
        Start-ScheduledTask -TaskName $taskName
        $deadline = (Get-Date).AddMinutes(10)
        do {
            Start-Sleep -Seconds 5
            $taskState = Get-ScheduledTask -TaskName $taskName
            $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
        } while (([string]$taskState.State -eq 'Running' -or $taskInfo.LastRunTime -le $previousRun) -and
            (Get-Date) -lt $deadline)

        if ((Get-Date) -ge $deadline -or $taskInfo.LastRunTime -le $previousRun) {
            throw 'Backup key was recovered, but the scheduled task did not complete within ten minutes.'
        }
        $scheduledResult = $taskInfo.LastTaskResult
        if ($scheduledResult -ne 0) {
            throw 'Backup key was recovered, but the scheduled backup task failed; the key remains installed.'
        }
        if (Test-Path -LiteralPath $archiveDirectory -PathType Container) {
            $archiveTimeFloor = $taskStartedAt.AddSeconds(-5)
            $newArchive = Get-ChildItem -LiteralPath $archiveDirectory -File -Filter '*.enc' |
                Where-Object { $_.LastWriteTime -ge $archiveTimeFloor -and $previousArchiveNames -notcontains $_.Name } |
                Select-Object -First 1
            $newLocalArchiveVerified = $null -ne $newArchive
        }
        if (-not $newLocalArchiveVerified) {
            throw 'Backup task returned success but no new verified encrypted archive was found.'
        }
    }

    [pscustomobject]@{
        code = 'beta_backup_key_recovered'
        existing_archive_authenticated = $true
        local_key_installed = $keyInstalled
        key_contents_logged = $false
        scheduled_task_started = [bool]$StartScheduledTask
        scheduled_task_result = $scheduledResult
        new_local_archive_verified = $newLocalArchiveVerified
    } | ConvertTo-Json -Compress
} finally {
    foreach ($temporaryFile in @($stagedKeyPath, $stagedArchivePath, $verifiedArchivePath)) {
        if ($temporaryFile -and (Test-Path -LiteralPath $temporaryFile -PathType Leaf)) {
            Remove-Item -LiteralPath $temporaryFile -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($temporaryDirectory in @($verifiedDirectory, $workDirectory)) {
        if ($temporaryDirectory -and (Test-Path -LiteralPath $temporaryDirectory -PathType Container) -and
            -not ((Get-Item -LiteralPath $temporaryDirectory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $remainingItems = @(Get-ChildItem -LiteralPath $temporaryDirectory -Force)
            if ($remainingItems.Count -eq 0) {
                Remove-Item -LiteralPath $temporaryDirectory -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
