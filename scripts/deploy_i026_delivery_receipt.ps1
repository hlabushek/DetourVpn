[CmdletBinding()]
param([switch]$RunHere)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$betaApiHost = '193.68.88.239'
$expectedHostname = 'hiplet-137728'
$keyPath = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if (-not $RunHere) {
    $shell = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if (-not $shell) { $shell = Get-Command powershell.exe -ErrorAction Stop }
    $arguments = '-NoExit -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
    Start-Process -FilePath $shell.Source -ArgumentList $arguments -WindowStyle Normal
    return
}
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw 'Beta SSH identity missing.' }

$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$scp = (Get-Command scp.exe -ErrorAction Stop).Source
$runId = [guid]::NewGuid().ToString('N')
$localStage = Join-Path $env:TEMP "detourvpn-i026-$runId"
$remoteStage = "/run/detourvpn-i026-$runId"
$remote = "root@$betaApiHost"
$sshOptions = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', $keyPath)
$files = @(
    @{ Name = 'mini_app.py'; Path = 'services/api/src/vpn_api/auth/mini_app.py'; Baseline = 'bde61d2b611df94ba488ef88126f37904c3205441ae8a9034d852a37087a1f09' },
    @{ Name = 'service.py'; Path = 'services/api/src/vpn_api/auth/service.py'; Baseline = '792209d5eada875fdd4ad43a4dd56db13734cf0cd08016f3ac64f4987d8a6bb4' },
    @{ Name = 'models.py'; Path = 'services/api/src/vpn_api/db/models.py'; Baseline = 'dec3a483461d3786b78edcefa2f29f94bc999c6d81fed6973ecf43ce242828e9' },
    @{ Name = '0017_login_delivery_receipt.py'; Path = 'services/api/migrations/versions/0017_login_delivery_receipt.py'; Baseline = $null }
)

if (Test-Path -LiteralPath $localStage) { throw 'Refusing to reuse an existing temporary stage.' }
if ($runId -notmatch '^[0-9a-f]{32}$') { throw 'Generated deployment ID is invalid.' }
New-Item -ItemType Directory -Path $localStage | Out-Null
try {
    $hashes = @{}
    foreach ($file in $files) {
        $source = Join-Path $repoRoot $file.Path
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing source file: $($file.Path)" }
        Copy-Item -LiteralPath $source -Destination (Join-Path $localStage $file.Name)
        $hashes[$file.Name] = (Get-FileHash -LiteralPath (Join-Path $localStage $file.Name) -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    $null = & $ssh @sshOptions $remote 'test "$(hostname -s)" = hiplet-137728 && systemctl is-active --quiet detourvpn-beta-api.service && systemctl is-active --quiet detourvpn-beta-database.service && test "$(curl --silent --output /dev/null --write-out "%{http_code}" http://127.0.0.1:28000/api/v1/ready)" = 200'
    if ($LASTEXITCODE -ne 0) { throw 'Beta API preflight failed; no files were changed.' }
    $null = & $ssh @sshOptions $remote "install -d -o root -g root -m 0700 '$remoteStage'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the unique root-only beta staging directory.' }
    foreach ($file in $files) {
        & $scp @sshOptions (Join-Path $localStage $file.Name) "${remote}:$remoteStage/$($file.Name)"
        if ($LASTEXITCODE -ne 0) { throw "Could not upload $($file.Name)." }
    }

    $remoteScript = @'
set -Eeuo pipefail
set +x
rid="$1"
[[ "$rid" =~ ^[0-9a-f]{32}$ ]] || { echo invalid_run_id >&2; exit 20; }
stage="/run/detourvpn-i026-$rid"
backup="/root/detourvpn-i026-pre-0017-$rid"
envfile="/run/detourvpn-i026-migrate-$rid.env"
migration_target=/opt/detourvpn-beta/services/api/migrations/versions/0017_login_delivery_receipt.py
api_stopped=0
source_changed=0
restore_sources() {
  cp -a "$backup/source/mini_app.py" /opt/detourvpn-beta/services/api/src/vpn_api/auth/mini_app.py
  cp -a "$backup/source/service.py" /opt/detourvpn-beta/services/api/src/vpn_api/auth/service.py
  cp -a "$backup/source/models.py" /opt/detourvpn-beta/services/api/src/vpn_api/db/models.py
}
cleanup() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$source_changed" = 1 ]; then
    systemctl stop detourvpn-beta-api.service || true
    restore_sources || true
    revision="$(docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"' 2>/dev/null)" || revision=unknown
    if [ "$revision" = 0016_beta_invites ]; then rm -f -- "$migration_target"; fi
    systemctl start detourvpn-beta-api.service || true
    api_stopped=0
    source_changed=0
  fi
  if [ "$api_stopped" = 1 ]; then systemctl start detourvpn-beta-api.service || true; fi
  rm -rf -- "$stage"
  rm -f -- "$envfile"
  exit "$rc"
}
trap cleanup EXIT
test "$(hostname -s)" = "__HOST__" || { echo beta_api_host_mismatch >&2; exit 21; }
test "$(stat -c '%a' /root)" = 700 || { echo root_backup_directory_not_private >&2; exit 21; }
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet detourvpn-beta-database.service
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200
test "$(docker inspect --format '{{.State.Health.Status}}' detourvpn-beta-postgres-1)" = healthy
revision="$(docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"')"
if [ "$revision" = 0016_beta_invites ]; then
  test ! -e "$migration_target"
elif [ "$revision" = 0017_login_delivery_receipt ]; then
  test "$(sha256sum "$migration_target" | cut -d ' ' -f1)" = __H_MIG__ || { echo installed_migration_differs_from_local_source >&2; exit 25; }
else
  echo unexpected_database_revision >&2; exit 26
fi
test ! -e "$backup" && test -d "$stage"
for item in 'mini_app.py:__H_MINI__' 'service.py:__H_SERVICE__' 'models.py:__H_MODELS__' '0017_login_delivery_receipt.py:__H_MIG__'; do
  file="${item%%:*}"; expected="${item#*:}"
  test "$(sha256sum "$stage/$file" | cut -d ' ' -f1)" = "$expected" || { echo uploaded_hash_mismatch >&2; exit 22; }
done
for item in 'mini_app.py:__B_MINI__' 'service.py:__B_SERVICE__' 'models.py:__B_MODELS__'; do
  file="${item%%:*}"; expected="${item#*:}"
  case "$file" in
    mini_app.py) target=/opt/detourvpn-beta/services/api/src/vpn_api/auth/mini_app.py ;;
    service.py) target=/opt/detourvpn-beta/services/api/src/vpn_api/auth/service.py ;;
    models.py) target=/opt/detourvpn-beta/services/api/src/vpn_api/db/models.py ;;
  esac
  test "$(sha256sum "$target" | cut -d ' ' -f1)" = "$expected" || { echo remote_source_changed_since_baseline >&2; exit 23; }
done
test "$(sha256sum /opt/detourvpn-beta/scripts/closed_migrate.py | cut -d ' ' -f1)" = 53569ac6cbe6ce5ff50c93eafed537bd0aa6765684ca598857c85dc152267b6f || { echo migration_runner_mismatch >&2; exit 24; }
umask 077
install -d -o root -g root -m 0700 "$backup" "$backup/source"
cp -a /opt/detourvpn-beta/services/api/src/vpn_api/auth/mini_app.py "$backup/source/mini_app.py"
cp -a /opt/detourvpn-beta/services/api/src/vpn_api/auth/service.py "$backup/source/service.py"
cp -a /opt/detourvpn-beta/services/api/src/vpn_api/db/models.py "$backup/source/models.py"
docker exec detourvpn-beta-postgres-1 sh -c 'set -eu; export PGPASSWORD="$(cat /run/secrets/pg_migrator)"; exec pg_dump -Fc -h 127.0.0.1 -U vpn_migrator -d detourvpn_beta' > "$backup/database.dump"
test -s "$backup/database.dump"
docker exec -i detourvpn-beta-postgres-1 pg_restore --list >/dev/null < "$backup/database.dump"
chmod 0600 "$backup/database.dump"
sha256sum "$backup/database.dump" | awk '{print "database_backup_sha256=" $1}'
printf 'database_backup_bytes='; stat -c '%s' "$backup/database.dump"
if [ "$revision" = 0016_beta_invites ]; then python3 - "$envfile" <<'PY'
from pathlib import Path
from urllib.parse import quote
import os, sys
password = Path('/etc/detourvpn-beta/secrets/pg-migrator').read_text(encoding='utf-8').rstrip('\n')
if not password or '\n' in password or '\r' in password:
    raise SystemExit('migration_secret_file_invalid')
url = 'postgresql+psycopg://vpn_migrator:' + quote(password, safe='') + '@127.0.0.1:25433/detourvpn_beta'
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w', encoding='utf-8') as stream:
    stream.write('VPN_DATABASE_URL=' + url + '\n')
PY
fi
install_next() {
  src="$1"; target="$2"; new="$target.i026-$rid"
  install -o "$(stat -c '%U' "$target")" -g "$(stat -c '%G' "$target")" -m "$(stat -c '%a' "$target")" "$stage/$src" "$new"
  mv -f -- "$new" "$target"
}
source_changed=1
systemctl stop detourvpn-beta-api.service
api_stopped=1
install_next mini_app.py /opt/detourvpn-beta/services/api/src/vpn_api/auth/mini_app.py
install_next service.py /opt/detourvpn-beta/services/api/src/vpn_api/auth/service.py
install_next models.py /opt/detourvpn-beta/services/api/src/vpn_api/db/models.py
if [ "$revision" = 0016_beta_invites ]; then
  test ! -e "$migration_target"
  install -o root -g detourvpn-beta -m 0640 "$stage/0017_login_delivery_receipt.py" "$migration_target"
  if ! systemd-run --quiet --wait --pipe --collect --unit="detourvpn-i026-migrate-$rid" --property=WorkingDirectory=/opt/detourvpn-beta --property="EnvironmentFile=$envfile" /opt/detourvpn-beta/.venv/bin/python scripts/closed_migrate.py; then
    revision="$(docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"')"
    restore_sources
    if [ "$revision" = 0016_beta_invites ]; then rm -f -- "$migration_target"; fi
    if systemctl start detourvpn-beta-api.service; then api_stopped=0; fi
    source_changed=0
    echo beta_migration_failed_sources_restored >&2
    exit 30
  fi
fi
rm -f -- "$envfile"
test "$(docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"')" = 0017_login_delivery_receipt
wait_for_api_ready() {
  systemctl start detourvpn-beta-api.service || return 1
  for attempt in $(seq 1 30); do
    if systemctl is-active --quiet detourvpn-beta-api.service && test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200; then return 0; fi
    sleep 1
  done
  return 1
}
if ! wait_for_api_ready; then
  restore_sources
  systemctl stop detourvpn-beta-api.service || true
  if wait_for_api_ready; then api_stopped=0; fi
  source_changed=0
  echo beta_api_failed_readiness_sources_restored_schema_kept >&2
  exit 30
fi
api_stopped=0
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:28000/mini-app/)" = 200
test "$(curl --silent --output /dev/null --write-out '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:28000/api/v1/auth/mini-app/request-code)" = 422
printf 'api_ready_http=200\nmini_app_http=200\nempty_otp_request_http=422\n'
printf 'database_revision=0017_login_delivery_receipt\nsource_backup=%s\n' "$(basename "$backup")"
printf 'source_hashes\n'
sha256sum /opt/detourvpn-beta/services/api/src/vpn_api/auth/mini_app.py /opt/detourvpn-beta/services/api/src/vpn_api/auth/service.py /opt/detourvpn-beta/services/api/src/vpn_api/db/models.py "$migration_target"
printf 'database_backup=/root/%s/database.dump\n' "$(basename "$backup")"
source_changed=0
'@ -replace '__HOST__', $expectedHostname
    $remoteScript = $remoteScript.Replace('__H_MINI__', $hashes['mini_app.py']).Replace('__H_SERVICE__', $hashes['service.py']).Replace('__H_MODELS__', $hashes['models.py']).Replace('__H_MIG__', $hashes['0017_login_delivery_receipt.py'])
    $remoteScript = $remoteScript.Replace('__B_MINI__', $files[0].Baseline).Replace('__B_SERVICE__', $files[1].Baseline).Replace('__B_MODELS__', $files[2].Baseline).Replace("`r", '')
    $remoteScript | & $ssh @sshOptions $remote 'bash -s' $runId
    if ($LASTEXITCODE -ne 0) { throw "Beta API update failed or rolled back (exit $LASTEXITCODE). Preserve backup; do not retry blindly." }
    Write-Output 'I-026 beta API update completed; output contains no user credentials or OTP.'
}
finally {
    if (Test-Path -LiteralPath $localStage) {
        $tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
        $resolvedStage = [IO.Path]::GetFullPath($localStage)
        if ([IO.Path]::GetDirectoryName($resolvedStage) -ne $tempRoot -or
            [IO.Path]::GetFileName($resolvedStage) -ne "detourvpn-i026-$runId") {
            throw 'Refusing to remove a temporary path outside the owned I-026 staging folder.'
        }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
    if ((Test-Path -LiteralPath $keyPath -PathType Leaf) -and $runId -match '^[0-9a-f]{32}$') {
        $null = & $ssh @sshOptions $remote "rm -rf -- '$remoteStage'" 2>$null
    }
}
