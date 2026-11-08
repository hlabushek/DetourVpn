[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$identity = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$scp = (Get-Command scp.exe -ErrorAction Stop).Source
$options = @('-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-i',$identity)
$remote = 'root@193.68.88.239'
$hostName = 'hiplet-137728'
$tag = [guid]::NewGuid().ToString('N')
$localSql = Join-Path $env:TEMP "i030-resolution-readonly-$tag.sql"
$remoteSql = "/run/i030-resolution-readonly-$tag.sql"
$sql = @'
BEGIN READ ONLY;
SELECT 'revision|' || version_num FROM alembic_version;
SELECT 'resolution_count|' || count(*)::text FROM peer_window_resolutions;
SELECT 'applied_full_debits|' || count(*)::text FROM quota_operations WHERE source_type='peer_window_resolution' AND kind='debit' AND status='applied' AND amount_bytes=1048576;
SELECT 'unknown_receipts|' || count(*)::text FROM peer_window_receipts WHERE observation='unknown' AND observed_bytes IS NULL;
SELECT 'non_closed_connections|' || count(*)::text FROM peer_connections WHERE state <> 'closed';
SELECT 'unresolved_unknown_bytes|' || coalesce(sum(h.amount_bytes),0)::text FROM peer_window_holds h JOIN peer_window_receipts r ON r.window_id=h.id AND r.user_id=h.user_id LEFT JOIN peer_window_resolutions p ON p.window_id=h.id AND p.user_id=h.user_id WHERE r.observation='unknown' AND p.window_id IS NULL;
ROLLBACK;
'@

try {
    [IO.File]::WriteAllText($localSql,$sql,[Text.UTF8Encoding]::new($false))
    & $scp @options $localSql "${remote}:$remoteSql" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'read_only_stage_failed' }
    $command = @'
set -eu
trap 'rm -f -- __SQL_PATH__' EXIT
test "$(hostname -s)" = '__HOST_NAME__'
docker exec -i detourvpn-beta-postgres-1 sh -c 'PGPASSWORD="$(cat /run/secrets/pg_runtime)" exec psql -X -qAt -v ON_ERROR_STOP=1 -h 127.0.0.1 -U vpn_api -d detourvpn_beta' < __SQL_PATH__
'@
    $command = $command.Replace('__SQL_PATH__',$remoteSql).Replace('__HOST_NAME__',$hostName)
    $output = @(& $ssh @options $remote $command 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'read_only_query_failed' }
    $lines = @($output | Where-Object { $_ -match '^(revision|resolution_count|applied_full_debits|unknown_receipts|non_closed_connections|unresolved_unknown_bytes)\|' })
    if ($lines.Count -ne 6) { throw 'read_only_result_incomplete' }
    foreach ($line in $lines) { Write-Output "beta_readonly_$line" }
} catch {
    Write-Output 'beta_readonly_resolution_check=unavailable; identifiers_and_secrets=not_exported'
    exit 1
} finally {
    Remove-Item -LiteralPath $localSql -Force -ErrorAction SilentlyContinue
}
