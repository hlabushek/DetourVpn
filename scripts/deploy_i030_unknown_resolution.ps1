[CmdletBinding()]
param([switch]$Execute)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$hostAddress = '193.68.88.239'
$expectedHost = 'hiplet-137728'
$sshIdentity = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$oldAdminHash = '5002f9387acc6bd8538741988f6daa3e4997264f64ca4230ecdd1f7d8a19a296'
$oldModelsHash = '55232a72f35eb17d7e38e4dd66fcb30d9b0a00f8a2242bbe4fd08e2f49512ddd'
$migrationRunnerHash = '53569ac6cbe6ce5ff50c93eafed537bd0aa6765684ca598857c85dc152267b6f'
$runId = [guid]::NewGuid().ToString('N')
$remoteStage = "/run/detourvpn-i030-resolution-$runId"
$remoteBackup = "/root/detourvpn-i030-pre-0018-$runId"
$remoteEnvFile = "/run/detourvpn-i030-migrate-$runId.env"
$localStage = Join-Path $env:TEMP "detourvpn-i030-resolution-$runId"
$remote = "root@$hostAddress"
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$scp = (Get-Command scp.exe -ErrorAction Stop).Source
$sshOptions = @(
    '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'IdentitiesOnly=yes', '-i', $sshIdentity
)

if ($runId -notmatch '^[0-9a-f]{32}$' -or !(Test-Path -LiteralPath $sshIdentity -PathType Leaf)) {
    throw 'The approved isolated-beta identity or generated run ID is unavailable.'
}

function Get-GitBlobHash([string]$RelativePath) {
    $code = @'
import hashlib, subprocess, sys
data = subprocess.run(["git", "show", "HEAD:" + sys.argv[1]], check=True, stdout=subprocess.PIPE).stdout
sys.stdout.write(hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest())
'@
    $value = & uv run --no-sync python -c $code $RelativePath
    if ($LASTEXITCODE -ne 0 -or $value -notmatch '^[0-9a-f]{64}$') {
        throw "Could not derive the checked-in baseline for $RelativePath."
    }
    return [string]$value
}

$files = @(
    @{ Name='admin.py'; Relative='services/api/src/vpn_api/admin.py'; Remote='/opt/detourvpn-beta/services/api/src/vpn_api/admin.py'; Baseline=$oldAdminHash },
    @{ Name='models.py'; Relative='services/api/src/vpn_api/db/models.py'; Remote='/opt/detourvpn-beta/services/api/src/vpn_api/db/models.py'; Baseline=$oldModelsHash },
    @{ Name='contracts.py'; Relative='services/api/src/vpn_api/quota/contracts.py'; Remote='/opt/detourvpn-beta/services/api/src/vpn_api/quota/contracts.py'; Baseline=(Get-GitBlobHash 'services/api/src/vpn_api/quota/contracts.py') },
    @{ Name='quota_service.py'; Relative='services/api/src/vpn_api/quota/service.py'; Remote='/opt/detourvpn-beta/services/api/src/vpn_api/quota/service.py'; Baseline=(Get-GitBlobHash 'services/api/src/vpn_api/quota/service.py') },
    @{ Name='window_funding.py'; Relative='services/api/src/vpn_api/tunnels/window_funding.py'; Remote='/opt/detourvpn-beta/services/api/src/vpn_api/tunnels/window_funding.py'; Baseline=(Get-GitBlobHash 'services/api/src/vpn_api/tunnels/window_funding.py') },
    @{ Name='window_resolution.py'; Relative='services/api/src/vpn_api/tunnels/window_resolution.py'; Remote='/opt/detourvpn-beta/services/api/src/vpn_api/tunnels/window_resolution.py'; Baseline=$null },
    @{ Name='0018_peer_window_resolution.py'; Relative='services/api/migrations/versions/0018_peer_window_resolution.py'; Remote='/opt/detourvpn-beta/services/api/migrations/versions/0018_peer_window_resolution.py'; Baseline=$null }
)

$localHashes = @{}
foreach ($file in $files) {
    $source = Join-Path $repo $file.Relative
    if (!(Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing local I-030 source: $($file.Relative)" }
    $localHashes[$file.Name] = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
}

$readOnlyPreflight = @'
set -eu
printf 'host=%s\n' "$(hostname -s)"
printf 'api=%s\n' "$(systemctl is-active detourvpn-beta-api.service)"
printf 'db_service=%s\n' "$(systemctl is-active detourvpn-beta-database.service)"
printf 'ready=%s\n' "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)"
printf 'pg_health=%s\n' "$(docker inspect --format '{{.State.Health.Status}}' detourvpn-beta-postgres-1)"
    printf 'revision=%s\n' "$(docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"')"
printf 'root_free_kb=%s\n' "$(df -Pk / | awk 'NR==2 {print $4}')"
printf 'mem_available_kb=%s\n' "$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
printf 'admin=%s\n' "$(sha256sum /opt/detourvpn-beta/services/api/src/vpn_api/admin.py | cut -d ' ' -f1)"
printf 'models=%s\n' "$(sha256sum /opt/detourvpn-beta/services/api/src/vpn_api/db/models.py | cut -d ' ' -f1)"
printf 'contracts=%s\n' "$(sed 's/\r$//' /opt/detourvpn-beta/services/api/src/vpn_api/quota/contracts.py | sha256sum | cut -d ' ' -f1)"
printf 'quota_service=%s\n' "$(sed 's/\r$//' /opt/detourvpn-beta/services/api/src/vpn_api/quota/service.py | sha256sum | cut -d ' ' -f1)"
printf 'window_funding=%s\n' "$(sed 's/\r$//' /opt/detourvpn-beta/services/api/src/vpn_api/tunnels/window_funding.py | sha256sum | cut -d ' ' -f1)"
    printf 'migration_runner=%s\n' "$(sha256sum /opt/detourvpn-beta/scripts/closed_migrate.py | cut -d ' ' -f1)"
    if [ -f /opt/detourvpn-beta/services/api/migrations/versions/0018_peer_window_resolution.py ]; then
      printf 'migration_0018=%s\n' "$(sha256sum /opt/detourvpn-beta/services/api/migrations/versions/0018_peer_window_resolution.py | cut -d ' ' -f1)"
    else
      printf 'migration_0018=absent\n'
    fi
test ! -e /opt/detourvpn-beta/services/api/src/vpn_api/tunnels/window_resolution.py
'@
$preflightRaw = @(& $ssh @sshOptions $remote $readOnlyPreflight)
if ($LASTEXITCODE -ne 0) { throw 'Read-only beta preflight failed; deployment was not attempted.' }
$preflight = @{}
foreach ($line in $preflightRaw) { if ($line -match '^([^=]+)=(.*)$') { $preflight[$Matches[1]] = $Matches[2] } }
if ($preflight.host -ne $expectedHost -or $preflight.api -ne 'active' -or
    $preflight.db_service -ne 'active' -or $preflight.ready -ne '200' -or
    $preflight.pg_health -ne 'healthy' -or
    $preflight.revision -notin @('0017_login_delivery_receipt', '0018_peer_window_resolution') -or
    $preflight.admin -ne $oldAdminHash -or $preflight.models -ne $oldModelsHash -or
    $preflight.contracts -ne $files[2].Baseline -or
    $preflight.quota_service -ne $files[3].Baseline -or
    $preflight.window_funding -ne $files[4].Baseline -or
    $preflight.migration_runner -ne $migrationRunnerHash -or
    [long]$preflight.root_free_kb -lt 1000000 -or [long]$preflight.mem_available_kb -lt 350000) {
    throw 'Beta preflight did not match the reviewed host, source baselines, revision or resource guard.'
}
$codeOnly = $preflight.revision -eq '0018_peer_window_resolution'
if (($codeOnly -and $preflight.migration_0018 -ne $localHashes['0018_peer_window_resolution.py']) -or
    (-not $codeOnly -and $preflight.migration_0018 -ne 'absent')) {
    throw 'The beta migration file state does not match the expected one-time upgrade path.'
}

if ($codeOnly) {
    Write-Output 'target=isolated_beta; api=active; database=healthy; revision=0018; migration=already_applied; source_baselines=matched'
} else {
    Write-Output 'target=isolated_beta; api=active; database=healthy; revision=0017; source_baselines=matched; disk_and_memory=adequate'
}
if (-not $Execute) { Write-Output 'mode=preflight_only; use -Execute to apply the reviewed I-030 beta update'; return }

if (Test-Path -LiteralPath $localStage) { throw 'Refusing to reuse an existing local temporary stage.' }
New-Item -ItemType Directory -Path $localStage | Out-Null
try {
    foreach ($file in $files) {
        Copy-Item -LiteralPath (Join-Path $repo $file.Relative) -Destination (Join-Path $localStage $file.Name)
        if ((Get-FileHash -LiteralPath (Join-Path $localStage $file.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $localHashes[$file.Name]) {
            throw "Staged source changed while preparing $($file.Name)."
        }
    }
    $createStage = "set -eu; test ! -e '$remoteStage'; install -d -o root -g root -m 0700 '$remoteStage'; printf 'stage=ready\n'"
    $stageResult = @(& $ssh @sshOptions $remote $createStage)
    if ($LASTEXITCODE -ne 0 -or $stageResult -notcontains 'stage=ready') { throw 'Remote staging directory could not be prepared.' }
    foreach ($file in $files) {
        & $scp @sshOptions (Join-Path $localStage $file.Name) "${remote}:$remoteStage/$($file.Name)"
        if ($LASTEXITCODE -ne 0) { throw "Could not stage $($file.Name) on isolated beta." }
    }

    $bash = @'
set -Eeuo pipefail
set +x
rid="$1"
stage="/run/detourvpn-i030-resolution-$rid"
backup="/root/detourvpn-i030-pre-0018-$rid"
envfile="/run/detourvpn-i030-migrate-$rid.env"
root=/opt/detourvpn-beta
api="$root/services/api/src/vpn_api"
migration="$root/services/api/migrations/versions/0018_peer_window_resolution.py"
changed=0
revision=unknown
phase=read_only_preflight
restore_sources() {
  for f in admin.py models.py contracts.py quota_service.py window_funding.py; do
    case "$f" in
      admin.py) dst="$api/admin.py" ;;
      models.py) dst="$api/db/models.py" ;;
      contracts.py) dst="$api/quota/contracts.py" ;;
      quota_service.py) dst="$api/quota/service.py" ;;
      window_funding.py) dst="$api/tunnels/window_funding.py" ;;
    esac
    cp -a "$backup/source/$f" "$dst"
  done
  if [ -f "$backup/source/window_resolution.py" ]; then
    cp -a "$backup/source/window_resolution.py" "$api/tunnels/window_resolution.py"
  else
    rm -f -- "$api/tunnels/window_resolution.py"
  fi
}
cleanup_stage() { rm -f -- "$stage"/*; rmdir -- "$stage" 2>/dev/null || true; rm -f -- "$envfile"; }
rollback() {
  rc=$?
  echo rollback_phase="$phase"
  if [ "$changed" = 1 ]; then
    systemctl stop detourvpn-beta-api.service || true
    restore_sources || true
    revision="$(docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"' 2>/dev/null)" || revision=unknown
    if [ "$revision" = 0017_login_delivery_receipt ]; then rm -f -- "$migration"; fi
    systemctl start detourvpn-beta-api.service || true
    for i in $(seq 1 30); do
      if [ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true)" = 200 ]; then
        echo rollout=code_rolled_back
        cleanup_stage
        exit "$rc"
      fi
      sleep 1
    done
    echo rollout=rollback_readiness_failed
    exit 70
  fi
  cleanup_stage
  exit "$rc"
}
trap rollback ERR
phase=verify_beta_service_state
test "$(hostname -s)" = '__HOST__'
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet detourvpn-beta-database.service
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200
test "$(docker inspect --format '{{.State.Health.Status}}' detourvpn-beta-postgres-1)" = healthy
revision="$(docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"')"
phase=verify_revision_and_migration_state
case "$revision" in
  0017_login_delivery_receipt)
    test ! -e "$migration"
    migration_mode=apply
    ;;
  0018_peer_window_resolution)
    test "$(sha256sum "$migration" | cut -d ' ' -f1)" = '__NEW_MIGRATION__'
    migration_mode=already_applied
    ;;
  *) echo unexpected_database_revision; false ;;
esac
phase=verify_beta_source_baselines
test "$(sha256sum /opt/detourvpn-beta/services/api/src/vpn_api/admin.py | cut -d ' ' -f1)" = '__BASE_ADMIN__'
test "$(sha256sum /opt/detourvpn-beta/services/api/src/vpn_api/db/models.py | cut -d ' ' -f1)" = '__BASE_MODELS__'
test "$(sed 's/\r$//' /opt/detourvpn-beta/services/api/src/vpn_api/quota/contracts.py | sha256sum | cut -d ' ' -f1)" = '__BASE_CONTRACTS__'
test "$(sed 's/\r$//' /opt/detourvpn-beta/services/api/src/vpn_api/quota/service.py | sha256sum | cut -d ' ' -f1)" = '__BASE_QUOTA__'
test "$(sed 's/\r$//' /opt/detourvpn-beta/services/api/src/vpn_api/tunnels/window_funding.py | sha256sum | cut -d ' ' -f1)" = '__BASE_FUNDING__'
test "$(sha256sum /opt/detourvpn-beta/scripts/closed_migrate.py | cut -d ' ' -f1)" = '__MIGRATOR__'
test ! -e "$backup"
test "$(stat -c '%a' /root)" = 700
phase=verify_uploaded_hashes
for item in 'admin.py:__NEW_ADMIN__' 'models.py:__NEW_MODELS__' 'contracts.py:__NEW_CONTRACTS__' 'quota_service.py:__NEW_QUOTA__' 'window_funding.py:__NEW_FUNDING__' 'window_resolution.py:__NEW_RESOLUTION__' '0018_peer_window_resolution.py:__NEW_MIGRATION__'; do
  f="${item%%:*}"; expected="${item#*:}"
  test "$(sha256sum "$stage/$f" | cut -d ' ' -f1)" = "$expected"
done
phase=source_and_database_backup
umask 077
install -d -o root -g root -m 0700 "$backup" "$backup/source"
for item in 'admin.py:admin.py' 'models.py:db/models.py' 'contracts.py:quota/contracts.py' 'quota_service.py:quota/service.py' 'window_funding.py:tunnels/window_funding.py'; do
  f="${item%%:*}"; dst="${item#*:}"
  cp -a "$api/$dst" "$backup/source/$f"
done
if [ -e "$api/tunnels/window_resolution.py" ]; then cp -a "$api/tunnels/window_resolution.py" "$backup/source/window_resolution.py"; fi
if [ "$migration_mode" = apply ]; then
  docker exec detourvpn-beta-postgres-1 sh -c 'set -eu; export PGPASSWORD="$(cat /run/secrets/pg_migrator)"; exec pg_dump -Fc -h 127.0.0.1 -U vpn_migrator -d detourvpn_beta' > "$backup/database.dump"
  test -s "$backup/database.dump"
  docker exec -i detourvpn-beta-postgres-1 pg_restore --list >/dev/null < "$backup/database.dump"
  chmod 0600 "$backup/database.dump"
  printf 'database_backup_bytes=%s\n' "$(stat -c '%s' "$backup/database.dump")"
  printf 'database_backup_sha256=%s\n' "$(sha256sum "$backup/database.dump" | cut -d ' ' -f1)"
python3 - "$envfile" <<'PY'
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
changed=1
phase=replace_source_and_migrate
systemctl stop detourvpn-beta-api.service
install_next() {
  src="$1"; target="$2"; new="$target.i030-$rid"
  install -o "$(stat -c '%U' "$target")" -g "$(stat -c '%G' "$target")" -m "$(stat -c '%a' "$target")" "$stage/$src" "$new"
  mv -f -- "$new" "$target"
}
install_next admin.py "$api/admin.py"
install_next models.py "$api/db/models.py"
install_next contracts.py "$api/quota/contracts.py"
install_next quota_service.py "$api/quota/service.py"
install_next window_funding.py "$api/tunnels/window_funding.py"
install -o root -g detourvpn-beta -m 0640 "$stage/window_resolution.py" "$api/tunnels/window_resolution.py"
if [ "$migration_mode" = apply ]; then
  install -o root -g detourvpn-beta -m 0640 "$stage/0018_peer_window_resolution.py" "$migration"
  systemd-run --quiet --wait --pipe --collect --unit="detourvpn-i030-migrate-$rid" --property=WorkingDirectory="$root" --property="EnvironmentFile=$envfile" "$root/.venv/bin/python" scripts/closed_migrate.py >/dev/null
fi
test "$(docker exec detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" psql -h 127.0.0.1 -U vpn_api -d detourvpn_beta -Atqc "SELECT version_num FROM alembic_version"')" = 0018_peer_window_resolution
rm -f -- "$envfile"
phase=api_startup
python3 -c 'import ast,pathlib; ast.parse(pathlib.Path("/opt/detourvpn-beta/services/api/src/vpn_api/admin.py").read_text()); ast.parse(pathlib.Path("/opt/detourvpn-beta/services/api/src/vpn_api/tunnels/window_resolution.py").read_text())'
systemctl start detourvpn-beta-api.service
for attempt in $(seq 1 30); do
  if systemctl is-active --quiet detourvpn-beta-api.service && test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true)" = 200; then break; fi
  [ "$attempt" -lt 30 ] || { echo rollout=readiness_failed; false; }
  sleep 1
done
phase=source_hash_verification
test "$(sha256sum "$api/admin.py" | cut -d ' ' -f1)" = '__NEW_ADMIN__'
test "$(sha256sum "$api/db/models.py" | cut -d ' ' -f1)" = '__NEW_MODELS__'
test "$(sha256sum "$api/quota/contracts.py" | cut -d ' ' -f1)" = '__NEW_CONTRACTS__'
test "$(sha256sum "$api/quota/service.py" | cut -d ' ' -f1)" = '__NEW_QUOTA__'
test "$(sha256sum "$api/tunnels/window_funding.py" | cut -d ' ' -f1)" = '__NEW_FUNDING__'
test "$(sha256sum "$api/tunnels/window_resolution.py" | cut -d ' ' -f1)" = '__NEW_RESOLUTION__'
test "$(sha256sum "$migration" | cut -d ' ' -f1)" = '__NEW_MIGRATION__'
phase=unauthenticated_route_probe
test "$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data '{"operation":"resolve_unknown_window","idempotency_key":"00000000-0000-0000-0000-000000000001","reason":"TEST ONLY unauthenticated deployment probe","user_id":"00000000-0000-0000-0000-000000000001","resource_id":"00000000-0000-0000-0000-000000000002","evidence_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' http://127.0.0.1:28000/api/admin/v1/actions)" = 401
systemctl is-active --quiet detourvpn-beta-api.service
echo rollout=success
echo readiness=200
echo database_revision=0018_peer_window_resolution
echo unauthenticated_admin_action=401
echo source_hashes=verified
changed=0
trap - ERR
cleanup_stage
'@
    $replacements = @{
        '__HOST__'=$expectedHost; '__BASE_ADMIN__'=$oldAdminHash; '__BASE_MODELS__'=$oldModelsHash
        '__BASE_CONTRACTS__'=$files[2].Baseline; '__BASE_QUOTA__'=$files[3].Baseline
        '__BASE_FUNDING__'=$files[4].Baseline; '__MIGRATOR__'=$migrationRunnerHash
        '__NEW_ADMIN__'=$localHashes['admin.py']; '__NEW_MODELS__'=$localHashes['models.py']
        '__NEW_CONTRACTS__'=$localHashes['contracts.py']; '__NEW_QUOTA__'=$localHashes['quota_service.py']
        '__NEW_FUNDING__'=$localHashes['window_funding.py']; '__NEW_RESOLUTION__'=$localHashes['window_resolution.py']
        '__NEW_MIGRATION__'=$localHashes['0018_peer_window_resolution.py']
    }
    foreach ($key in $replacements.Keys) { $bash = $bash.Replace($key, [string]$replacements[$key]) }
    $bash = $bash.Replace("`r", '')
    $localScript = Join-Path $env:TEMP "detourvpn-i030-resolution-$runId.sh"
    [IO.File]::WriteAllText($localScript, $bash, [Text.UTF8Encoding]::new($false))
    try {
        & $scp @sshOptions $localScript "${remote}:$remoteStage/deploy.sh"
        if ($LASTEXITCODE -ne 0) { throw 'Could not stage the beta installer.' }
        $remoteOutput = @(& $ssh @sshOptions $remote "chmod 0700 '$remoteStage/deploy.sh' && bash '$remoteStage/deploy.sh' '$runId'")
        $remoteCode = $LASTEXITCODE
        if ($remoteCode -ne 0 -or $remoteOutput -notcontains 'rollout=success') {
            $remoteOutput | Where-Object { $_ -match '^(rollout=|rollback_phase=)' } | ForEach-Object { Write-Output $_ }
            throw 'Beta API migration/deployment did not report success; preserve the server backup and do not retry blindly.'
        }
        $receipt = [ordered]@{
            issue='I-030'; timestamp_utc=[DateTime]::UtcNow.ToString('o'); target='isolated_beta_control_plane'
            revision_before=$(if ($codeOnly) { '0018_peer_window_resolution' } else { '0017_login_delivery_receipt' })
            revision_after='0018_peer_window_resolution'; migration_already_applied=$codeOnly
            source_files=$localHashes; api_ready_http=200; unauthenticated_admin_action_http=401
            database_dump_created_and_validated=(-not $codeOnly); result='success'
        }
        $receiptPath = Join-Path $repo ("docs\evidence\i-030-unknown-resolution-beta-deploy-{0}.json" -f [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
        [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
        if ($codeOnly) {
            Write-Output 'beta_deployment=success; database_revision=0018; readiness=200; migration=unchanged; secrets_and_identifiers=not_exported'
        } else {
            Write-Output 'beta_deployment=success; database_revision=0018; readiness=200; migration=applied; backup=validated; secrets_and_identifiers=not_exported'
        }
        Write-Output "sanitized_receipt=$receiptPath"
    } finally {
        Remove-Item -LiteralPath $localScript -Force -ErrorAction SilentlyContinue
    }
} finally {
    if (Test-Path -LiteralPath $localStage) {
        $tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
        $resolvedStage = [IO.Path]::GetFullPath($localStage)
        if ([IO.Path]::GetDirectoryName($resolvedStage) -ne $tempRoot -or
            [IO.Path]::GetFileName($resolvedStage) -ne "detourvpn-i030-resolution-$runId") {
            throw 'Refusing to remove a temporary path outside the generated I-030 staging folder.'
        }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
    $cleanup = "rm -f -- '$remoteStage'/*; rmdir -- '$remoteStage' 2>/dev/null || true"
    $null = & $ssh @sshOptions $remote $cleanup 2>$null
}
