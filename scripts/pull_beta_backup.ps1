[CmdletBinding()]
param([switch]$RunHere)

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
$remoteArchive = '/var/lib/detourvpn-beta-backups/latest.enc'
$maximumArchiveBytes = 64MB

if (-not $RunHere) {
    $shell = Get-Command pwsh.exe,powershell.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $shell) { throw 'PowerShell executable is unavailable.' }
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    Start-Process -FilePath $shell.Source -ArgumentList $arguments -WindowStyle Normal
    return
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Project Python runtime is missing.' }
if (-not (Test-Path -LiteralPath $identityPath -PathType Leaf)) { throw 'Beta SSH identity is missing.' }
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Local beta backup key is missing.' }
if (Test-Path -LiteralPath $backupDir -PathType Leaf) { throw 'Local backup directory is not a directory.' }
if (-not (Test-Path -LiteralPath $backupDir -PathType Container)) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}
if ((Get-Item -LiteralPath $backupDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Local backup directory must not be a reparse point.'
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
$incoming = Join-Path $env:TEMP ('detourvpn-beta-backup-' + [guid]::NewGuid().ToString('N') + '.enc')

try {
    & $sshPath @sshOptions $remote 'systemctl start detourvpn-beta-backup.service' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Beta backup service did not complete successfully.' }

    $sizeText = (& $sshPath @sshOptions $remote "/usr/bin/stat -c %s -- $remoteArchive" 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $sizeText -notmatch '^\d{1,10}$') {
        throw 'Beta backup archive is unavailable.'
    }
    $remoteSize = [long]$sizeText
    if ($remoteSize -le 0 -or $remoteSize -gt $maximumArchiveBytes) {
        throw 'Beta backup archive size is outside the accepted limit.'
    }

    & $scpPath @sshOptions "${remote}:$remoteArchive" $incoming | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Encrypted beta backup transfer failed.' }
    if (-not (Test-Path -LiteralPath $incoming -PathType Leaf)) { throw 'Transferred archive is missing.' }
    if ((Get-Item -LiteralPath $incoming).Length -ne $remoteSize) {
        throw 'Transferred archive size does not match the beta host.'
    }

    & $pythonPath (Join-Path $PSScriptRoot 'import_beta_backup.py') `
        --source $incoming --destination-dir $backupDir --key-file $keyPath
    if ($LASTEXITCODE -ne 0) { throw 'Encrypted backup verification failed.' }
} finally {
    Remove-Item -LiteralPath $incoming -Force -ErrorAction SilentlyContinue
}
