[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$betaHost = '193.68.88.239'
$keyPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$expectedBootstrapSha256 = '78a0c9c88641eb7b9877ca2f7720dddb1288e674cbc5144549983575eb909562'

if (-not $RunHere) {
    $pwshPath = (Get-Command pwsh.exe -ErrorAction Stop).Source
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}" -RunHere' -f $PSCommandPath
    Start-Process -FilePath $pwshPath -ArgumentList $arguments -WindowStyle Normal
    return
}

if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Beta SSH identity missing.' }
$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$scpPath = (Get-Command scp.exe -ErrorAction Stop).Source
$runId = [guid]::NewGuid().ToString('N')
$remoteScript = "/run/detourvpn-beta-admin-$runId.sh"
$localScript = Join-Path $env:TEMP "detourvpn-beta-admin-$runId.sh"

$body = @'
#!/bin/bash
set -Eeuo pipefail
umask 077
cd /opt/detourvpn-beta
systemctl is-active --quiet detourvpn-beta-api.service || { echo beta_api_not_active >&2; exit 20; }
test "$(sha256sum /opt/detourvpn-beta/scripts/admin_bootstrap.py | cut -d ' ' -f 1)" = "__BOOTSTRAP_SHA256__" || {
    echo admin_bootstrap_source_mismatch >&2
    exit 21
}
set -a
. /etc/detourvpn-beta/api.env
set +a
export PYTHONPATH=/opt/detourvpn-beta/services/api/src
preflight_file="/run/detourvpn-admin-preflight-$RANDOM-$RANDOM"
.venv/bin/python - > "$preflight_file" <<'PY'
from sqlalchemy import select
from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine
from vpn_api.db.models import AdminIdentity

settings = Settings(_env_file=None)
safe_mode = (
    settings.environment == "closed"
    and settings.enable_admin
    and settings.closed_admin_only
    and not settings.enable_auth
    and not settings.enable_mini_app
    and not settings.beta_invites_only
    and settings.admin_encryption_key is not None
    and settings.telegram_bot_token is None
)
if not safe_mode:
    raise SystemExit("beta_admin_preflight_failed")
engine = create_database_engine(settings)
try:
    with engine.connect() as connection:
        identities = list(
            connection.execute(select(AdminIdentity.username, AdminIdentity.disabled_at))
        )
finally:
    engine.dispose()
if not identities:
    bootstrap_username = "owner"
elif (
    len(identities) == 1
    and identities[0].username == "owner"
    and identities[0].disabled_at is not None
):
    bootstrap_username = "beta-owner"
else:
    raise SystemExit("admin_identity_state_not_safe; no changes made")
print(bootstrap_username)
PY
bootstrap_username="$(cat "$preflight_file")"
rm -f -- "$preflight_file"
case "$bootstrap_username" in
    owner|beta-owner) ;;
    *) echo admin_identity_state_not_safe >&2; exit 22 ;;
esac
printf '%s\n' 'beta_admin_preflight_passed; no token/auth/ingress changes'

enrollment_dir="/run/detourvpn-beta-admin-$RANDOM-$RANDOM"
install -d -o root -g root -m 0700 "$enrollment_dir"
.venv/bin/python scripts/admin_bootstrap.py \
    --username "$bootstrap_username" \
    --enrollment-file "$enrollment_dir/$bootstrap_username.uri" \
    --show-enrollment-in-terminal
status=$?
if test "$status" -eq 0 && test ! -e "$enrollment_dir/$bootstrap_username.uri"; then
    rmdir -- "$enrollment_dir"
fi
exit "$status"
'@
$body = $body.Replace('__BOOTSTRAP_SHA256__', $expectedBootstrapSha256)

[IO.File]::WriteAllText($localScript, $body.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
$remote = "root@$betaHost"
$sshOptions = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', $keyPath)

try {
    & $sshPath @sshOptions $remote "test ! -e $remoteScript"
    if ($LASTEXITCODE -ne 0) { throw 'Remote temporary path check failed.' }
    & $scpPath @sshOptions $localScript "${remote}:$remoteScript"
    if ($LASTEXITCODE -ne 0) { throw 'Private bootstrap script transfer failed.' }
    Write-Host 'Beta-only administrator enrollment. No Bot API token is requested.'
    Write-Host 'Choose a unique password, import the private TOTP URI into your authenticator, and enter its current code.'
    Write-Host 'Do not copy the URI, password, or code into chat or screenshots.'
    & $sshPath -tt @sshOptions $remote "chmod 0700 -- $remoteScript && bash -- $remoteScript"
    $bootstrapExit = $LASTEXITCODE
    if ($bootstrapExit -eq 0) {
        Write-Host 'Beta admin MFA bootstrap finished. Confirm the non-secret completion line above.'
    } else {
        Write-Host 'Bootstrap did not complete. Do not send credentials or the enrollment URI to chat.'
    }
} finally {
    & $sshPath @sshOptions $remote "rm -f -- $remoteScript" 2>$null
    [IO.File]::Delete($localScript)
}

Read-Host 'Press Enter to close this beta setup window' | Out-Null
if ($bootstrapExit -ne 0) { exit $bootstrapExit }
