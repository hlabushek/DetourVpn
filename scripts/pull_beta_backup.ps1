[CmdletBinding()]
param(
    [switch]$RunHere,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaHost = '193.68.88.239'
$remote = "root@$betaHost"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$identityPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$secretRoot = Join-Path $env:LOCALAPPDATA 'DetourVpn\beta-backup'
$backupDir = Join-Path $secretRoot 'archives'
$keyPath = Join-Path $secretRoot 'backup.key'
$runStatusPath = Join-Path $secretRoot 'last-run-status.json'
$preflightStatusPath = Join-Path $secretRoot 'last-preflight-status.json'
$remoteArchive = '/var/lib/detourvpn-beta-backups/latest.enc'
$maximumArchiveBytes = 64MB
$stage = 'startup'
$failureCode = 'unclassified_error'
$incoming = $null

function Write-BackupStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Result,
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$FailureCode
    )

    $temporaryPath = $null
    $replacementBackupPath = $null
    try {
        if (-not (Test-Path -LiteralPath $secretRoot -PathType Container)) { return }
        $rootItem = Get-Item -LiteralPath $secretRoot -Force -ErrorAction Stop
        if ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { return }

        if (Test-Path -LiteralPath $Path) {
            $statusItem = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
            if ($statusItem.PSIsContainer -or
                ($statusItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return }
        }

        $record = [ordered]@{
            schema_version = 1
            recorded_at_utc = [DateTime]::UtcNow.ToString('o')
            result = $Result
            stage = $Stage
            failure_code = $FailureCode
        }
        $json = ConvertTo-Json -InputObject $record -Compress
        $temporaryPath = Join-Path $secretRoot ('.backup-status-' + [guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::WriteAllText($temporaryPath, $json, [Text.UTF8Encoding]::new($false))

        if (Test-Path -LiteralPath $Path) {
            $replacementBackupPath = $Path + '.' + [guid]::NewGuid().ToString('N') + '.bak'
            [IO.File]::Replace($temporaryPath, $Path, $replacementBackupPath)
            Remove-Item -LiteralPath $replacementBackupPath -Force -ErrorAction SilentlyContinue
            $replacementBackupPath = $null
        } else {
            [IO.File]::Move($temporaryPath, $Path)
        }
        $temporaryPath = $null
    } catch {
        # Diagnostics must never change the backup result or expose exception text.
    } finally {
        if ($temporaryPath -and (Test-Path -LiteralPath $temporaryPath)) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
        if ($replacementBackupPath -and (Test-Path -LiteralPath $replacementBackupPath)) {
            Remove-Item -LiteralPath $replacementBackupPath -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not $RunHere) {
    $shell = Get-Command pwsh.exe,powershell.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $shell) { throw 'PowerShell executable is unavailable.' }
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    if ($PreflightOnly) { $arguments += ' -PreflightOnly' }
    Start-Process -FilePath $shell.Source -ArgumentList $arguments -WindowStyle Normal
    return
}

try {
    $stage = 'local_preflight'
    $failureCode = 'secret_directory_invalid'
    if (-not (Test-Path -LiteralPath $secretRoot -PathType Container)) {
        throw 'Local beta backup directory is missing.'
    }
    if ((Get-Item -LiteralPath $secretRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Local beta backup directory must not be a reparse point.'
    }

    $failureCode = 'python_runtime_missing'
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw 'Project Python runtime is missing.'
    }
    $failureCode = 'ssh_identity_missing'
    if (-not (Test-Path -LiteralPath $identityPath -PathType Leaf)) {
        throw 'Beta SSH identity is missing.'
    }
    $failureCode = 'backup_key_missing'
    if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
        throw 'Local beta backup key is missing.'
    }
    if ((Get-Item -LiteralPath $keyPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        $failureCode = 'backup_key_invalid'
        throw 'Local beta backup key must not be a reparse point.'
    }

    $failureCode = 'archive_directory_invalid'
    if (Test-Path -LiteralPath $backupDir -PathType Leaf) {
        throw 'Local backup directory is not a directory.'
    }
    if ((Test-Path -LiteralPath $backupDir -PathType Container) -and
        ((Get-Item -LiteralPath $backupDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Local backup directory must not be a reparse point.'
    }
    if ($PreflightOnly -and -not (Test-Path -LiteralPath $backupDir -PathType Container)) {
        throw 'Local backup archive directory is missing.'
    }

    if ($PreflightOnly) {
        $failureCode = ''
        Write-BackupStatus -Path $preflightStatusPath -Result 'passed' `
            -Stage 'local_preflight_complete' -FailureCode ''
        Write-Output '{"code":"backup_preflight_passed","network_contacted":false}'
        return
    }

    $stage = 'prepare_local_directory'
    $failureCode = 'archive_directory_create_failed'
    if (-not (Test-Path -LiteralPath $backupDir -PathType Container)) {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    }
    if ((Get-Item -LiteralPath $backupDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Local backup directory must not be a reparse point.'
    }

    $stage = 'local_tools'
    $failureCode = 'ssh_client_missing'
    $sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
    $failureCode = 'scp_client_missing'
    $scpPath = (Get-Command scp.exe -ErrorAction Stop).Source
    $sshOptions = @(
        '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=15',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'IdentitiesOnly=yes',
        '-i', $identityPath
    )
    $incoming = Join-Path $env:TEMP ('detourvpn-beta-backup-' + [guid]::NewGuid().ToString('N') + '.enc')

    $stage = 'remote_backup_service'
    $failureCode = 'remote_backup_service_failed'
    & $sshPath @sshOptions $remote 'systemctl start detourvpn-beta-backup.service' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Beta backup service did not complete successfully.' }

    $stage = 'remote_archive_stat'
    $failureCode = 'remote_archive_unavailable'
    $sizeText = (& $sshPath @sshOptions $remote "/usr/bin/stat -c %s -- $remoteArchive" 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $sizeText -notmatch '^\d{1,10}$') {
        throw 'Beta backup archive is unavailable.'
    }
    $remoteSize = [long]$sizeText
    $failureCode = 'remote_archive_size_invalid'
    if ($remoteSize -le 0 -or $remoteSize -gt $maximumArchiveBytes) {
        throw 'Beta backup archive size is outside the accepted limit.'
    }

    $stage = 'archive_transfer'
    $failureCode = 'archive_transfer_failed'
    & $scpPath @sshOptions "${remote}:$remoteArchive" $incoming 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Encrypted beta backup transfer failed.' }
    $failureCode = 'transferred_archive_missing'
    if (-not (Test-Path -LiteralPath $incoming -PathType Leaf)) { throw 'Transferred archive is missing.' }

    $stage = 'archive_size_validation'
    $failureCode = 'archive_size_mismatch'
    if ((Get-Item -LiteralPath $incoming).Length -ne $remoteSize) {
        throw 'Transferred archive size does not match the beta host.'
    }

    $stage = 'local_archive_import'
    $failureCode = 'archive_import_failed'
    & $pythonPath (Join-Path $PSScriptRoot 'import_beta_backup.py') `
        --source $incoming --destination-dir $backupDir --key-file $keyPath
    if ($LASTEXITCODE -ne 0) { throw 'Encrypted backup verification failed.' }

    $failureCode = ''
    Write-BackupStatus -Path $runStatusPath -Result 'succeeded' `
        -Stage 'complete' -FailureCode ''
} catch {
    if ([string]::IsNullOrWhiteSpace($failureCode)) { $failureCode = 'unclassified_error' }
    Write-BackupStatus -Path $runStatusPath -Result 'failed' `
        -Stage $stage -FailureCode $failureCode
    throw
} finally {
    if ($incoming -and (Test-Path -LiteralPath $incoming)) {
        Remove-Item -LiteralPath $incoming -Force -ErrorAction SilentlyContinue
    }
}
