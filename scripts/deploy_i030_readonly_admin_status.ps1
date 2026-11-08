[CmdletBinding()]
param([switch]$Execute)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repo 'services\api\src\vpn_api\admin.py'
$hostIp = '193.68.88.239'; $hostname = 'hiplet-137728'
$remoteSource = '/opt/detourvpn-beta/services/api/src/vpn_api/admin.py'
$old = '28af25563d57fa5d6b6ecc27ea596aab012debf3550567978a93202a294a1919'
$new = '5002f9387acc6bd8538741988f6daa3e4997264f64ca4230ecdd1f7d8a19a296'
$key = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
if (-not (Test-Path -LiteralPath $key -PathType Leaf)) { throw 'Existing beta SSH identity unavailable.' }
if ((Get-FileHash $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $new) { throw 'Local admin.py differs from reviewed version.' }
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source; $scp = (Get-Command scp.exe -ErrorAction Stop).Source
$opts = @('-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-i',$key)
$remote = "root@$hostIp"; $id = [Guid]::NewGuid().ToString('N'); $stage = "/run/detourvpn-i030-admin-$id"
$check = @"
set -eu
test "`$(hostname -s)" = $hostname
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet detourvpn-beta-database.service
test "`$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200
test "`$(sha256sum $remoteSource | cut -d ' ' -f1)" = $old
printf 'beta_preflight=passed\n'
"@
$checkOut = @(& $ssh @opts $remote $check)
if ($LASTEXITCODE -ne 0 -or $checkOut -notcontains 'beta_preflight=passed') { throw 'Beta preflight failed.' }
Write-Host "host=$hostname; api=active; database=active; readiness=200; source=expected"
if (-not $Execute) { Write-Host 'mode=preflight_only; rerun with -Execute to deploy'; return }
$bash = @'
set -euo pipefail
s='__STAGE__'; f='__SOURCE__'; old='__OLD__'; new='__NEW__'; changed=0
cleanup() { rm -f -- "$s/admin.py" "$s/deploy.sh" "$s/admin.py.before"; rmdir -- "$s" 2>/dev/null || true; }
rollback() {
  rc=$?
  if [ "$changed" = 1 ] && [ -f "$s/admin.py.before" ]; then
    install -o root -g detourvpn-beta -m 0640 "$s/admin.py.before" "$f.rollback"
    mv -f -- "$f.rollback" "$f"; systemctl restart detourvpn-beta-api.service || true
    for i in $(seq 1 30); do
      if [ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true)" = 200 ]; then echo beta_rollout=rolled_back; cleanup; exit "$rc"; fi
      sleep 1
    done
    echo beta_rollout=rollback_health_failed; echo recovery_copy="$s/admin.py.before"; exit 70
  fi
  cleanup; exit "$rc"
}
trap rollback ERR
[ "$(hostname -s)" = hiplet-137728 ]
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet detourvpn-beta-database.service
[ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200 ]
[ "$(sha256sum "$f" | cut -d ' ' -f1)" = "$old" ]
[ "$(stat -c '%U:%G:%a' "$f")" = root:detourvpn-beta:640 ]
[ "$(sha256sum "$s/admin.py" | cut -d ' ' -f1)" = "$new" ]
python3 -c 'import ast,pathlib,sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' "$s/admin.py"
cp --preserve=mode,ownership,timestamps -- "$f" "$s/admin.py.before"
install -o root -g detourvpn-beta -m 0640 "$s/admin.py" "$f.next"; mv -f -- "$f.next" "$f"; changed=1
systemctl restart detourvpn-beta-api.service
ok=0
for i in $(seq 1 30); do if [ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready || true)" = 200 ]; then ok=1; break; fi; sleep 1; done
[ "$ok" = 1 ]
[ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/admin/v1/users/00000000-0000-0000-0000-000000000001/window-status)" = 401 ]
[ "$(sha256sum "$f" | cut -d ' ' -f1)" = "$new" ]
changed=0; trap - ERR; systemctl is-active --quiet detourvpn-beta-api.service
echo beta_rollout=success; echo readiness=200; echo unauthenticated_status_route=401; echo module_sha256="$new"
cleanup
'@
$bash = $bash.Replace('__STAGE__',$stage).Replace('__SOURCE__',$remoteSource).Replace('__OLD__',$old).Replace('__NEW__',$new)
$tmp = Join-Path $env:TEMP "detourvpn-i030-$id.sh"
[IO.File]::WriteAllText($tmp,$bash.Replace("`r`n","`n"),[Text.UTF8Encoding]::new($false))
try {
  $mk = "set -eu; test ! -e '$stage'; install -d -o root -g root -m 0700 '$stage'; printf 'beta_stage=created\n'"
  $mkOut = @(& $ssh @opts $remote $mk)
  if ($LASTEXITCODE -ne 0 -or $mkOut -notcontains 'beta_stage=created') { throw 'Could not create unique /run stage.' }
  & $scp @opts $source "${remote}:$stage/admin.py"; if ($LASTEXITCODE -ne 0) { throw 'Module upload failed.' }
  & $scp @opts $tmp "${remote}:$stage/deploy.sh"; if ($LASTEXITCODE -ne 0) { throw 'Installer upload failed.' }
  $out = @(& $ssh @opts $remote "chmod 0700 '$stage/deploy.sh' && bash '$stage/deploy.sh'"); $code = $LASTEXITCODE
  $out | ForEach-Object { Write-Host $_ }
  if ($code -ne 0 -or $out -notcontains 'beta_rollout=success') { throw 'Deployment not confirmed; do not test Connect.' }
  $receipt = [ordered]@{ issue='I-030'; timestamp_utc=[DateTime]::UtcNow.ToString('o'); target='isolated beta control plane'; hostname=$hostname; service='detourvpn-beta-api.service'; changed_file='services/api/src/vpn_api/admin.py'; previous_sha256=$old; deployed_sha256=$new; readiness_http=200; unauthenticated_status_route_http=401; migrations='none'; database_changes='none'; result='success' }
  $path = Join-Path $repo ("docs\evidence\i-030-admin-status-endpoint-deploy-{0}.json" -f [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
  [IO.File]::WriteAllText($path,($receipt | ConvertTo-Json),[Text.UTF8Encoding]::new($false)); Write-Host "sanitized_receipt=$path"
} finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
