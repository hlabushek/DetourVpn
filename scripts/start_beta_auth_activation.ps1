[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaHost = '193.68.88.239'
$keyPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$appRoot = Join-Path $repoRoot 'apps\telegram-mini-app'
$releaseId = [guid]::NewGuid().ToString('N')
$releaseDir = Join-Path $appRoot ".cache\beta-mini-app-$releaseId"
$archivePath = Join-Path $repoRoot ".cache\beta-mini-app-$releaseId.tar.gz"
$helperPath = Join-Path $PSScriptRoot 'beta_activate_auth.py'
$remoteHelper = "/opt/detourvpn-beta/scripts/beta_activate_auth-$releaseId.py"
$remoteArchive = "/tmp/beta-mini-app-$releaseId.tar.gz"
$remoteScriptPath = "/tmp/beta-auth-activate-$releaseId.sh"
$localScriptPath = Join-Path $env:TEMP "beta-auth-activate-$releaseId.sh"
$remote = "root@$betaHost"

if (-not $RunHere) {
    $pwshPath = (Get-Command pwsh.exe -ErrorAction Stop).Source
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    Start-Process -FilePath $pwshPath -ArgumentList $arguments -WindowStyle Normal
    return
}

if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Beta SSH identity missing.' }
if (Test-Path -LiteralPath $releaseDir) { throw 'Refusing to overwrite a Mini App build output.' }
$cacheDir = Join-Path $appRoot '.cache'
if (-not (Test-Path -LiteralPath $cacheDir -PathType Container)) {
    New-Item -ItemType Directory -Path $cacheDir | Out-Null
}
$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$scpPath = (Get-Command scp.exe -ErrorAction Stop).Source
$tarPath = (Get-Command tar.exe -ErrorAction Stop).Source
$sshOptions = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', $keyPath)
$remoteRelease = "/opt/detourvpn-beta/mini-app/releases/$releaseId"
$remoteScript = @'
set -Eeuxo pipefail
release_id="$1"
release_dir="/opt/detourvpn-beta/mini-app/releases/$release_id"
helper="/opt/detourvpn-beta/scripts/beta_activate_auth-$release_id.py"
archive="/tmp/beta-mini-app-$release_id.tar.gz"
test "$release_id" = "$(basename "$release_dir")"
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet caddy
test ! -e "$release_dir"
test -s "$helper"
python3 - <<'PY'
from pathlib import Path
import re
p=Path('/etc/detourvpn-beta/api.env')
d={}
for line in p.read_text().splitlines():
    if line.startswith('VPN_') and '=' in line:
        key,value=line.split('=',1); d[key]=value
required={'VPN_ENVIRONMENT':'closed','VPN_PUBLIC_ORIGIN':'https://127.0.0.1','VPN_ENABLE_ADMIN':'true','VPN_CLOSED_ADMIN_ONLY':'true','VPN_ENABLE_AUTH':'false','VPN_ENABLE_MINI_APP':'false','VPN_BETA_INVITES_ONLY':'false'}
if any(d.get(k)!=v for k,v in required.items()): raise SystemExit('beta_closed_mode_preflight_failed')
token=d.get('VPN_TELEGRAM_BOT_TOKEN','')
match=re.fullmatch(r'([0-9]{5,12}):[A-Za-z0-9_-]{30,128}',token)
if not match or d.get('VPN_TELEGRAM_BOT_ID')!=match.group(1) or d.get('VPN_TELEGRAM_BOT_USERNAME')!='uysdgyfudf_bot': raise SystemExit('beta_bot_config_preflight_failed')
if any(d.get(k) for k in ('VPN_AUTH_PEPPER','VPN_TELEGRAM_WEBHOOK_SECRET','VPN_MINI_APP_STATIC_DIR')): raise SystemExit('beta_auth_already_configured')
PY
install -d -o root -g detourvpn-beta -m 0750 /opt/detourvpn-beta/mini-app/releases
install -d -o root -g detourvpn-beta -m 0750 "$release_dir"
if tar -tzf "$archive" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then echo unsafe_archive_paths >&2; exit 31; fi
tar -xzf "$archive" --no-same-owner -C "$release_dir"
test -s "$release_dir/index.html"
chown -R root:detourvpn-beta "$release_dir"
find "$release_dir" -type d -exec chmod 0750 {} +
find "$release_dir" -type f -exec chmod 0640 {} +
chmod 0600 "$helper"
cd /opt/detourvpn-beta
.venv/bin/python "$helper" preflight --mini-app-dir "$release_dir"
.venv/bin/python "$helper" activate --mini-app-dir "$release_dir"
if ! systemctl restart detourvpn-beta-api.service || ! systemctl is-active --quiet detourvpn-beta-api.service; then
    .venv/bin/python "$helper" rollback --backup "$(ls -1t /etc/detourvpn-beta/api.env.pre-auth-* | head -n 1 | xargs -r basename)"
    systemctl restart detourvpn-beta-api.service || true
    echo beta_api_activation_rolled_back >&2
    exit 32
fi
for attempt in $(seq 1 15); do
    if curl --fail --silent --show-error http://127.0.0.1:28000/api/v1/ready >/dev/null && curl --fail --silent --show-error http://127.0.0.1:28000/mini-app/ >/dev/null; then
        echo beta_api_auth_invite_only_active; echo mini_app_local_route_http_200
        break
    fi
    if [ "$attempt" -eq 15 ]; then
        backup=$(find /etc/detourvpn-beta -maxdepth 1 -type f -name 'api.env.pre-auth-*' -printf '%T@ %f\n' | sort -nr | head -n 1 | cut -d ' ' -f 2-)
        .venv/bin/python "$helper" rollback --backup "$backup"
        systemctl restart detourvpn-beta-api.service || true
        echo beta_api_activation_rolled_back_after_readiness_failure >&2
        exit 33
    fi
    sleep 2
done
echo rollback_backup="$(find /etc/detourvpn-beta -maxdepth 1 -type f -name 'api.env.pre-auth-*' -printf '%f\n' | sort | tail -n 1)"
'@ -replace "`r", ''

try {
    [IO.File]::WriteAllText($localScriptPath, $remoteScript, [Text.UTF8Encoding]::new($false))
    & (Get-Command npm.cmd -ErrorAction Stop).Source --prefix $appRoot run build -- --outDir $releaseDir --emptyOutDir false --minify
    if ($LASTEXITCODE -ne 0) { throw 'Mini App build failed.' }
    if (-not (Test-Path -LiteralPath (Join-Path $releaseDir 'index.html') -PathType Leaf)) {
        throw 'Mini App build output has no index.html.'
    }
    & $tarPath -czf $archivePath -C $releaseDir .
    if ($LASTEXITCODE -ne 0) { throw 'Mini App packaging failed.' }
    & $sshPath @sshOptions $remote "test ! -e $remoteArchive && test ! -e $remoteHelper && test ! -e $remoteScriptPath"
    if ($LASTEXITCODE -ne 0) { throw 'Beta target staging paths are occupied.' }
    & $scpPath @sshOptions $archivePath "${remote}:$remoteArchive"
    if ($LASTEXITCODE -ne 0) { throw 'Mini App archive transfer failed.' }
    & $scpPath @sshOptions $helperPath "${remote}:$remoteHelper"
    if ($LASTEXITCODE -ne 0) { throw 'Beta activation helper transfer failed.' }
    & $scpPath @sshOptions $localScriptPath "${remote}:$remoteScriptPath"
    if ($LASTEXITCODE -ne 0) { throw 'Beta activation script transfer failed.' }
    $helperHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $helperPath).Hash.ToLowerInvariant()
    $remoteHash = (& $sshPath @sshOptions $remote "sha256sum $remoteHelper" | ForEach-Object { ($_ -split '\s+')[0] })
    if ($remoteHash -ne $helperHash) { throw 'Beta activation helper checksum mismatch.' }
    $sshCommand = "bash $remoteScriptPath $releaseId"
    & $sshPath @sshOptions $remote $sshCommand
    if ($LASTEXITCODE -ne 0) { throw 'Beta activation failed. Inspect the non-secret status above.' }
} finally {
    & $sshPath @sshOptions $remote "rm -f -- $remoteArchive $remoteHelper $remoteScriptPath" 2>$null
    Remove-Item -LiteralPath $localScriptPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
}

Write-Host "Beta API auth and invite-only activated. Versioned Mini App release: $releaseId"
Write-Host 'Public Caddy ingress remains closed; no Telegram/BotFather config was changed.'
