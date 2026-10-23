# Start only after the owner approved docs/runbooks/closed-rollout-proposal.md.
# The visible terminal is required for local, hidden sudo/token entry.
[CmdletBinding()]
param([switch]$RunHere, [switch]$StageOnly, [switch]$InstallStaged)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
if (-not $RunHere -and -not $StageOnly) {
    $taskLaunchArgs = @('-NoExit', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-RunHere')
    if ($InstallStaged) { $taskLaunchArgs += '-InstallStaged' }
    Start-Process powershell.exe -ArgumentList $taskLaunchArgs
    return
}
Set-Location -LiteralPath $taskRoot
Write-Host 'DetourVpn: approved control-plane rollout on B (85.209.132.205).'
Write-Host 'Enter the vpn-deploy sudo password only in this terminal.'
Write-Host 'Do not paste secrets into chat. Existing VPN and webhook stay untouched.'
$taskSsh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', '/home/ad/.ssh/vpn_staging_20261019')
if ($InstallStaged) {
    wsl -d kali-linux -- ssh -tt @taskSsh vpn-deploy@85.209.132.205 'sudo /bin/bash /home/vpn-deploy/detourvpn-rollout/infra/closed/install-b.sh'
    if ($LASTEXITCODE -ne 0) { throw 'Rollout stopped; inspect the terminal and private remote baseline' }
    return
}
$taskStage = Join-Path $taskRoot ('.cache/closed-release-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskStage | Out-Null
# Explicit source allowlist; no .env, caches, keys, builds or test credentials.
$taskFiles = @('pyproject.toml', 'uv.lock', 'alembic.ini')
foreach ($taskDirectory in @('services/api/src', 'services/api/migrations', 'apps/admin/static', 'infra/closed')) {
    $taskFiles += @(rg --files $taskDirectory | Where-Object { $_ -notmatch '(__pycache__|\.pyc$)' })
}
$taskFiles += @('scripts/closed_migrate.py', 'scripts/admin_bootstrap.py')
$taskManifest = [System.Collections.Generic.List[string]]::new()
foreach ($taskFile in $taskFiles) {
    $taskSource = Join-Path $taskRoot $taskFile
    $taskSourceItem = Get-Item -LiteralPath $taskSource
    # OneDrive placeholders are reparse points too; reject actual links only.
    if ($taskSourceItem.LinkType -in @('SymbolicLink', 'Junction')) { throw 'Symlink source refused' }
    $taskDestination = Join-Path $taskStage $taskFile
    New-Item -ItemType Directory -Path (Split-Path $taskDestination -Parent) -Force | Out-Null
    # Scripts/configs are text; Linux shell cannot parse CRLF in shebang/set lines.
    $taskText = [IO.File]::ReadAllText($taskSource).Replace("`r`n", "`n")
    [IO.File]::WriteAllText($taskDestination, $taskText, [Text.UTF8Encoding]::new($false))
    $taskHash = (Get-FileHash -LiteralPath $taskDestination -Algorithm SHA256).Hash.ToLowerInvariant()
    $taskManifest.Add($taskHash + '  ' + $taskFile.Replace('\', '/'))
}
[IO.File]::WriteAllText((Join-Path $taskStage 'release.sha256'), ($taskManifest -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
$taskConvertedPath = wsl -d kali-linux -- wslpath -a $taskStage.Replace('\', '/')
if ($LASTEXITCODE -ne 0 -or -not $taskConvertedPath) { throw 'WSL path conversion failed' }
$taskWslStage = $taskConvertedPath.Trim()
wsl -d kali-linux -- ssh @taskSsh vpn-deploy@85.209.132.205 'umask 077; test ! -e /home/vpn-deploy/detourvpn-rollout && mkdir /home/vpn-deploy/detourvpn-rollout'
if ($LASTEXITCODE -ne 0) { throw 'Remote staging already exists or SSH failed; inspect rather than overwrite' }
wsl -d kali-linux -- scp -r @taskSsh "$taskWslStage/." 'vpn-deploy@85.209.132.205:/home/vpn-deploy/detourvpn-rollout/'
if ($LASTEXITCODE -ne 0) { throw 'Source transfer failed' }
if ($StageOnly) { Write-Host 'Source staged; no privileged command executed.'; return }
wsl -d kali-linux -- ssh -tt @taskSsh vpn-deploy@85.209.132.205 'sudo /bin/bash /home/vpn-deploy/detourvpn-rollout/infra/closed/install-b.sh'
if ($LASTEXITCODE -ne 0) { throw 'Rollout stopped; retain terminal output and inspect private remote baseline' }
Write-Host 'Infrastructure phase finished. Auth, admin MFA enrollment and acceptance remain.'
