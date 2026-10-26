[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaHost = '193.68.88.239'
$keyPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$helperPath = Join-Path $repoRoot 'scripts\provision_beta_node.py'
$expectedServiceHash = '8f5ae9e07d61cb58b81bc116cc539df95fe67ddb40518ac0f95281a1b10bf85c'
$expectedOldHelperHash = '68865b8ddded9496817962f7a2dcf6ebe6020d02b76be2a0998dd72d1f456b7e'
$expectedHelperHash = '1563eceed748430fd86d815da2350efb3ffcbe9fe490a38a3569c82d75b9c0f3'

if (-not $RunHere) {
    $shellCommand = Get-Command pwsh.exe,powershell.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $shellCommand) { throw 'No PowerShell executable is available.' }
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    Start-Process -FilePath $shellCommand.Source -ArgumentList $arguments -WindowStyle Normal
    return
}

if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Beta SSH identity missing.' }
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) { throw 'Beta helper source is missing.' }
$helperHash = (Get-FileHash -LiteralPath $helperPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($helperHash -ne $expectedHelperHash) {
    throw 'Beta helper source changed after review; refresh the source hash first.'
}

$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$scpPath = (Get-Command scp.exe -ErrorAction Stop).Source
$sshOptions = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', $keyPath)
$remote = "root@$betaHost"
$preflight = @'
set -eu
cd /opt/detourvpn-beta
test "$(systemctl is-active detourvpn-beta-api.service)" = active
curl -fsS http://127.0.0.1:28000/api/v1/ready >/dev/null
service_hash=$(sha256sum services/api/src/vpn_api/nodes/service.py | cut -d ' ' -f 1)
helper_hash=$(sha256sum scripts/provision_beta_node.py | cut -d ' ' -f 1)
test "$service_hash" = '8f5ae9e07d61cb58b81bc116cc539df95fe67ddb40518ac0f95281a1b10bf85c'
test "$helper_hash" = '68865b8ddded9496817962f7a2dcf6ebe6020d02b76be2a0998dd72d1f456b7e'
grep -q '^VPN_ENVIRONMENT=closed$' /etc/detourvpn-beta/api.env
grep -q '^VPN_ENABLE_AUTH=true$' /etc/detourvpn-beta/api.env
grep -q '^VPN_ENABLE_MINI_APP=true$' /etc/detourvpn-beta/api.env
grep -q '^VPN_BETA_INVITES_ONLY=true$' /etc/detourvpn-beta/api.env
if grep -q '^VPN_ENABLE_WINDOW_TUNNELS=true$' /etc/detourvpn-beta/api.env; then exit 31; fi
echo beta_helper_update_preflight_passed
echo api_readiness=200
echo window_tunnels=disabled
'@ -replace "`r", ''

Write-Host 'Read-only preflight: beta API helper update only; no secrets are requested.'
$preflightOutput = @(& $sshPath @sshOptions $remote $preflight)
if ($LASTEXITCODE -ne 0 -or $preflightOutput -notcontains 'beta_helper_update_preflight_passed') {
    throw 'Beta API baseline did not match; no files were changed.'
}
$preflightOutput | ForEach-Object { Write-Host $_ }

$runId = [guid]::NewGuid().ToString('N')
$remoteHelper = "/tmp/beta-node-provision-$runId.py"
$remoteScript = "/tmp/beta-node-helper-update-$runId.sh"
$localScript = Join-Path $env:TEMP "beta-node-helper-update-$runId.sh"
$updateScript = @'
set -Eeuo pipefail
run_id="$1"
helper_hash="$2"
helper_source="/tmp/beta-node-provision-$run_id.py"
helper="/opt/detourvpn-beta/scripts/provision_beta_node.py"
backup="$helper.pre-beta-db-target-$run_id"
candidate="$helper.candidate-$run_id"
cleanup() { rm -f -- "$helper_source" "$candidate"; }
rollback() {
  install -o root -g root -m 0600 "$backup" "$helper.rollback-$run_id"
  mv -f -- "$helper.rollback-$run_id" "$helper"
}
trap cleanup EXIT
cd /opt/detourvpn-beta
test -f "$helper_source" && test ! -e "$backup" && test ! -e "$candidate"
printf '%s  %s\n' "$helper_hash" "$helper_source" | sha256sum -c - >/dev/null
test "$(sha256sum "$helper" | cut -d ' ' -f 1)" = '68865b8ddded9496817962f7a2dcf6ebe6020d02b76be2a0998dd72d1f456b7e'
cp -a -- "$helper" "$backup"
install -o root -g root -m 0600 "$helper_source" "$candidate"
mv -- "$candidate" "$helper"
if ! .venv/bin/python scripts/provision_beta_node.py --help >/dev/null; then
  rollback
  echo beta_helper_update_failed_rolled_back >&2
  exit 42
fi
echo beta_node_provisioning_helper_updated
printf 'rollback_backup=%s\n' "$(basename "$backup")"
'@ -replace "`r", ''

[IO.File]::WriteAllText($localScript, $updateScript, [Text.UTF8Encoding]::new($false))
try {
    & $scpPath @sshOptions $helperPath "${remote}:$remoteHelper"
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage beta provisioning helper.' }
    & $scpPath @sshOptions $localScript "${remote}:$remoteScript"
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage beta helper update script.' }
    & $sshPath @sshOptions $remote "bash $remoteScript $runId $helperHash"
    if ($LASTEXITCODE -ne 0) { throw 'Beta helper update failed; inspect its fixed status line.' }
} finally {
    Remove-Item -LiteralPath $localScript -Force -ErrorAction SilentlyContinue
    & $sshPath @sshOptions $remote "rm -f -- $remoteHelper $remoteScript" 2>$null | Out-Null
}

Write-Host 'Beta helper updated. API service was not restarted; API settings, Caddy, VPN node, B and production were unchanged.'
