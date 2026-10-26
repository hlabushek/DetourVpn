[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaHost = '193.68.88.239'
$betaNode = '95.81.73.183'
$keyPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$runId = [guid]::NewGuid().ToString('N')
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$templatePath = Join-Path $repoRoot 'infra\beta\Caddyfile.ip'
$localConfig = Join-Path $env:TEMP "Caddyfile-beta-auth-$runId"
$localScript = Join-Path $env:TEMP "caddy-beta-auth-$runId.sh"
$remoteConfig = "/tmp/Caddyfile-beta-auth-$runId"
$remoteScriptPath = "/tmp/caddy-beta-auth-$runId.sh"
$remote = "root@$betaHost"

if (-not $RunHere) {
    $pwshPath = (Get-Command pwsh.exe -ErrorAction Stop).Source
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    Start-Process -FilePath $pwshPath -ArgumentList $arguments -WindowStyle Normal
    return
}

if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Beta SSH identity missing.' }
$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$scpPath = (Get-Command scp.exe -ErrorAction Stop).Source
$sshOptions = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', $keyPath)
$rendered = (Get-Content -Raw -LiteralPath $templatePath).
    Replace('{$DETOURVPN_BETA_IP}', $betaHost).
    Replace('{$DETOURVPN_BETA_NODE_IP}', $betaNode)
if ($rendered -match '\{\$DETOURVPN_') { throw 'Unresolved beta Caddy placeholder.' }

$remoteScript = @'
set -Eeuo pipefail
config="$1"
run_id="$2"
backup="/etc/caddy/Caddyfile.pre-beta-auth-$run_id"
candidate="/etc/caddy/Caddyfile.candidate-$run_id"
test -f "$config"
test ! -e "$backup"
test ! -e "$candidate"
/usr/bin/caddy validate --config "$config" --adapter caddyfile
install -o root -g caddy -m 0640 "$config" "$candidate"
/usr/bin/caddy validate --config "$candidate" --adapter caddyfile
cp -a /etc/caddy/Caddyfile "$backup"
mv -f "$candidate" /etc/caddy/Caddyfile
if ! systemctl reload caddy || ! systemctl is-active --quiet caddy; then
  cp -a "$backup" "/etc/caddy/Caddyfile.rollback-$run_id"
  mv -f "/etc/caddy/Caddyfile.rollback-$run_id" /etc/caddy/Caddyfile
  systemctl reload caddy || true
  echo beta_caddy_reload_failed_rolled_back >&2
  exit 42
fi
echo beta_caddy_allowlist_active
printf 'backup=%s\n' "$(basename "$backup")"
'@ -replace "`r", ''

try {
    [IO.File]::WriteAllText($localConfig, $rendered, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($localScript, $remoteScript, [Text.UTF8Encoding]::new($false))
    & $scpPath @sshOptions $localConfig "${remote}:$remoteConfig"
    if ($LASTEXITCODE -ne 0) { throw 'Caddy config transfer failed.' }
    & $scpPath @sshOptions $localScript "${remote}:$remoteScriptPath"
    if ($LASTEXITCODE -ne 0) { throw 'Caddy activation script transfer failed.' }
    & $sshPath @sshOptions $remote "bash $remoteScriptPath $remoteConfig $runId"
    if ($LASTEXITCODE -ne 0) { throw 'Beta Caddy validation or reload failed.' }
} finally {
    & $sshPath @sshOptions $remote "rm -f -- $remoteConfig $remoteScriptPath" 2>$null
    Remove-Item -LiteralPath $localConfig,$localScript -Force -ErrorAction SilentlyContinue
}

Write-Host 'Beta Caddy allowlist is active. A/B and production were not touched.'
