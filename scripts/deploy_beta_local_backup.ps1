[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaHost = '193.68.88.239'
$remote = "root@$betaHost"
$identityPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$localSecretRoot = Join-Path $env:LOCALAPPDATA 'DetourVpn\beta-backup'
$localArchiveDir = Join-Path $localSecretRoot 'archives'
$localKeyPath = Join-Path $localSecretRoot 'backup.key'
$taskName = 'DetourVpn Beta Backup Daily'

if (-not $RunHere) {
    $shell = Get-Command pwsh.exe,powershell.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $shell) { throw 'PowerShell executable is unavailable.' }
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    Start-Process -FilePath $shell.Source -ArgumentList $arguments -WindowStyle Normal
    return
}

$sources = @(
    (Join-Path $PSScriptRoot 'beta_backup.py'),
    (Join-Path $repoRoot 'services\api\src\vpn_api\beta_backup.py'),
    (Join-Path $repoRoot 'infra\beta\detourvpn-beta-backup.service'),
    (Join-Path $repoRoot 'infra\beta\install-local-backup.sh'),
    (Join-Path $PSScriptRoot 'pull_beta_backup.ps1'),
    (Join-Path $PSScriptRoot 'import_beta_backup.py'),
    (Join-Path $PSScriptRoot 'register_beta_backup_task.ps1')
)
foreach ($path in @($identityPath, $pythonPath) + $sources) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'A required local backup setup file is missing.' }
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'The daily backup task already exists; inspect it before setup.'
}
if (Test-Path -LiteralPath $localSecretRoot) {
    if (-not (Test-Path -LiteralPath $localSecretRoot -PathType Container)) {
        throw 'Local beta backup path exists but is not a directory.'
    }
    if ((Get-Item -LiteralPath $localSecretRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Local beta backup directory must not be a reparse point.'
    }
    if (-not (Test-Path -LiteralPath $localKeyPath -PathType Leaf)) {
        throw 'Local beta backup folder exists without its key; inspect it before setup.'
    }
}

$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$scpPath = (Get-Command scp.exe -ErrorAction Stop).Source
$sshOptions = @('-o','BatchMode=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-i',$identityPath)
$preflight = @'
set -eu
cd /opt/detourvpn-beta
test "$(systemctl is-active detourvpn-beta-api.service)" = active
test "$(curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200
test -r /etc/detourvpn-beta/operations.env
grep -q '^VPN_DATABASE_URL=.' /etc/detourvpn-beta/operations.env
test "$(sha256sum scripts/beta_backup.py | cut -d ' ' -f 1)" = bad3533fe39e05b8da6d5d39fbd0ff7eaae531c1c5eeba0387af0e4320be225e
test "$(sha256sum services/api/src/vpn_api/beta_backup.py | cut -d ' ' -f 1)" = d0438503b855711905c0606db6d3f931bb57b43c41e588db500f4b2f16edb860
test ! -e /etc/systemd/system/detourvpn-beta-backup.service
if test -e /etc/detourvpn-beta/backup-secrets; then
  test -d /etc/detourvpn-beta/backup-secrets && test ! -L /etc/detourvpn-beta/backup-secrets
  test "$(stat -c '%U:%G:%a' /etc/detourvpn-beta/backup-secrets)" = root:detourvpn-beta:750
  test -f /etc/detourvpn-beta/backup-secrets/backup-key && test ! -L /etc/detourvpn-beta/backup-secrets/backup-key
  test "$(stat -c '%U:%G:%a' /etc/detourvpn-beta/backup-secrets/backup-key)" = root:detourvpn-beta:440
fi
if test -e /var/lib/detourvpn-beta-backups; then
  test -d /var/lib/detourvpn-beta-backups && test ! -L /var/lib/detourvpn-beta-backups
  test "$(stat -c '%U:%G:%a' /var/lib/detourvpn-beta-backups)" = detourvpn-beta:detourvpn-beta:700
  test ! -e /var/lib/detourvpn-beta-backups/latest.enc
fi
test "$(df -Pk / | awk 'NR==2 {print $4}')" -gt 1048576
echo beta_backup_preflight_passed
echo api_readiness=200
echo backup_unit=absent
echo backup_staging_state=prepared_without_archive
'@ -replace [char]13, ''

Write-Host 'Read-only preflight: only the isolated beta API backup path is in scope.'
$preflightOutput = @(& $sshPath @sshOptions $remote $preflight)
if ($LASTEXITCODE -ne 0 -or $preflightOutput -notcontains 'beta_backup_preflight_passed') {
    throw 'Beta backup preflight failed; no remote files were changed.'
}
$preflightOutput | ForEach-Object { Write-Host $_ }

$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$userFolderAcl = '*{0}:(OI)(CI)F' -f $userSid
$systemFolderAcl = '*S-1-5-18:(OI)(CI)F'
New-Item -ItemType Directory -Path $localArchiveDir -Force | Out-Null
& icacls.exe $localSecretRoot /inheritance:r /grant:r $userFolderAcl $systemFolderAcl | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict local backup folder access.' }
& icacls.exe $localArchiveDir /inheritance:r /grant:r $userFolderAcl $systemFolderAcl | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict local archive folder access.' }
if (-not (Test-Path -LiteralPath $localKeyPath -PathType Leaf)) {
    & $pythonPath (Join-Path $PSScriptRoot 'operations.py') keygen --key-file $localKeyPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Local backup key generation failed.' }
}
$validateKey = 'from cryptography.fernet import Fernet; import sys; Fernet(open(sys.argv[1], "rb").read())'
& $pythonPath -c $validateKey $localKeyPath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Local backup key could not be validated.' }
$userKeyAcl = '*{0}:F' -f $userSid
& icacls.exe $localKeyPath /inheritance:r /grant:r $userKeyAcl '*S-1-5-18:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict local backup key access.' }

$runId = [guid]::NewGuid().ToString('N')
$stage = "/root/.cache/detourvpn-beta-backup-$runId"
$scriptHash = (Get-FileHash -LiteralPath $sources[0] -Algorithm SHA256).Hash.ToLowerInvariant()
$moduleHash = (Get-FileHash -LiteralPath $sources[1] -Algorithm SHA256).Hash.ToLowerInvariant()
$unitHash = (Get-FileHash -LiteralPath $sources[2] -Algorithm SHA256).Hash.ToLowerInvariant()

try {
    & $sshPath @sshOptions $remote "/usr/bin/install -d -o root -g root -m 0700 $stage"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create private beta staging directory.' }
    $remoteFiles = @(
        [pscustomobject]@{ Local = $sources[0]; Remote = "$stage/beta_backup.py" },
        [pscustomobject]@{ Local = $sources[1]; Remote = "$stage/backup-module.py" },
        [pscustomobject]@{ Local = $sources[2]; Remote = "$stage/backup.service" },
        [pscustomobject]@{ Local = $sources[3]; Remote = "$stage/install-local-backup.sh" },
        [pscustomobject]@{ Local = $localKeyPath; Remote = "$stage/backup.key" }
    )
    foreach ($file in $remoteFiles) {
        & $scpPath @sshOptions $file.Local ($remote + ':' + $file.Remote)
        if ($LASTEXITCODE -ne 0) { throw 'Could not stage beta-only backup files.' }
    }
    & $sshPath @sshOptions $remote "/bin/bash $stage/install-local-backup.sh $runId $scriptHash $moduleHash $unitHash"
    if ($LASTEXITCODE -ne 0) { throw 'Beta backup service rollout failed; inspect its fixed status and rollback copy.' }
} finally {
    & $sshPath @sshOptions $remote "/bin/rm -f -- $stage/install-local-backup.sh $stage/beta_backup.py $stage/backup-module.py $stage/backup.service $stage/backup.key; /bin/rmdir -- $stage" 2>$null | Out-Null
}

& (Join-Path $PSScriptRoot 'pull_beta_backup.ps1') -RunHere
if ($LASTEXITCODE -ne 0) { throw 'Initial backup transfer/verification failed; daily task was not registered.' }
& (Join-Path $PSScriptRoot 'register_beta_backup_task.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Initial local backup passed, but daily task registration failed.' }
Write-Host 'Initial encrypted backup was verified locally; daily task is scheduled at 07:00.'
