[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
# Audit M9: hosts and key name come from an operator file outside the repository, with lines
#   BETA_HOST=<public IPv4 of the primary API host>
#   BETA_NODE_IP=<public IPv4 of the node to allow>
#   BETA_SSH_KEY=<key file name under %USERPROFILE%\.ssh>
$operatorEnv = Join-Path $env:USERPROFILE '.detourvpn\beta-operator.env'
if (-not (Test-Path -LiteralPath $operatorEnv -PathType Leaf)) { throw 'Operator env file missing.' }
$config = @{}
foreach ($line in Get-Content -LiteralPath $operatorEnv) {
    if ($line -match '^(BETA_HOST|BETA_NODE_IP|BETA_SSH_KEY)=(.+)$') { $config[$matches[1]] = $matches[2].Trim() }
}
foreach ($name in 'BETA_HOST', 'BETA_NODE_IP', 'BETA_SSH_KEY') {
    if (-not $config.ContainsKey($name)) { throw "Operator env file lacks $name." }
}
$betaHost = $config['BETA_HOST']
$betaNode = $config['BETA_NODE_IP']
$keyPath = Join-Path $env:USERPROFILE (Join-Path '.ssh' $config['BETA_SSH_KEY'])
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
$mergeScript = Join-Path $repoRoot 'scripts\caddy_allowlist.py'
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
# ADR-175: the live allowlist is merged, never replaced: the node IPs that connect_beta_node.py
# added earlier stay in the matcher, and this node's IP is added once.
$liveRaw = & $sshPath @sshOptions $remote 'cat /etc/caddy/Caddyfile'
if ($LASTEXITCODE -ne 0) { throw 'Could not read the live Caddy configuration.' }
$live = ($liveRaw -join "`n")
$rendered = ($live | & $pythonPath $mergeScript merge $betaNode) -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'Caddy allowlist merge refused (see caddy_allowlist.py codes).' }
if ($rendered -notmatch 'remote_ip') { throw 'Merged Caddy configuration has no node matcher.' }

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
