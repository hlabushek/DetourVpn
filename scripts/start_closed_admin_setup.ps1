[CmdletBinding()]
param([switch]$RunHere, [switch]$StageOnly, [switch]$InstallStaged)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
if (-not $RunHere -and -not $StageOnly) {
    $taskArgs = @('-NoExit', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-RunHere')
    if ($InstallStaged) { $taskArgs += '-InstallStaged' }
    Start-Process powershell.exe -ArgumentList $taskArgs
    return
}
$taskSsh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', '/home/ad/.ssh/vpn_staging_20261019')
if (-not $InstallStaged) {
    $taskStage = Join-Path $taskRoot ('.cache/admin-setup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $taskStage | Out-Null
    $taskFiles = @('services/api/src/vpn_api/config.py', 'infra/closed/finish-admin-b.sh', 'scripts/admin_bootstrap.py')
    $taskManifest = [System.Collections.Generic.List[string]]::new()
    foreach ($taskFile in $taskFiles) {
        $taskSource = Join-Path $taskRoot $taskFile
        if ((Get-Item -LiteralPath $taskSource).LinkType -in @('SymbolicLink', 'Junction')) { throw 'Link refused' }
        $taskDestination = Join-Path $taskStage $taskFile
        New-Item -ItemType Directory -Path (Split-Path $taskDestination -Parent) -Force | Out-Null
        [IO.File]::WriteAllText($taskDestination, [IO.File]::ReadAllText($taskSource).Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
        $taskManifest.Add((Get-FileHash -LiteralPath $taskDestination -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $taskFile)
    }
    [IO.File]::WriteAllText((Join-Path $taskStage 'release.sha256'), ($taskManifest -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
    $taskConvertedPath = wsl -d kali-linux -- wslpath -a $taskStage.Replace('\', '/')
    if ($LASTEXITCODE -ne 0 -or -not $taskConvertedPath) { throw 'WSL path conversion failed' }
    $taskWslStage = $taskConvertedPath.Trim()
    wsl -d kali-linux -- ssh @taskSsh vpn-deploy@85.209.132.205 'umask 077; test ! -e /home/vpn-deploy/detourvpn-admin-setup && mkdir /home/vpn-deploy/detourvpn-admin-setup'
    if ($LASTEXITCODE -ne 0) { throw 'Staging directory exists or SSH failed; inspect before overwrite' }
    wsl -d kali-linux -- scp -r @taskSsh "$taskWslStage/." 'vpn-deploy@85.209.132.205:/home/vpn-deploy/detourvpn-admin-setup/'
    if ($LASTEXITCODE -ne 0) { throw 'Transfer failed' }
}
if ($StageOnly) { Write-Host 'Admin setup source staged; no privileged command executed.'; return }
Write-Host 'DetourVpn: administrative setup on B. No Telegram tokens required.'
Write-Host 'Enter sudo password, then a NEW unique admin password twice.'
Write-Host 'Import the private enrollment URI into your authenticator and enter its current code.'
Write-Host 'No screenshots, recording, or secrets in chat.'
wsl -d kali-linux -- ssh -tt @taskSsh vpn-deploy@85.209.132.205 'sudo /bin/bash /home/vpn-deploy/detourvpn-admin-setup/infra/closed/finish-admin-b.sh'
if ($LASTEXITCODE -ne 0) { throw 'Administrative setup stopped; do not paste enrollment URI or passwords into chat' }
