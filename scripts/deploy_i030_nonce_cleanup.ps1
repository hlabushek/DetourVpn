[CmdletBinding()]
param([switch]$Execute)
# I-030/I-027: prune expired agent replay nonces so the beta backup fits its size limit.
# Preflight is read-only. -Execute replaces two files atomically (with rollback) and runs the
# existing maintenance unit once. No migration; no API restart (the files are imported lazily).
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = Split-Path -Parent $PSScriptRoot
$hostIp = '193.68.88.239'; $hostname = 'hiplet-137728'
$files = @(
  [ordered]@{ Local = 'services\api\src\vpn_api\nodes\service.py'; Remote = '/opt/detourvpn-beta/services/api/src/vpn_api/nodes/service.py'; Old = '8f5ae9e07d61cb58b81bc116cc539df95fe67ddb40518ac0f95281a1b10bf85c'; New = 'f486cce1f0089266dca37ecfc578e7ba56f12c316b98346a8e08362251dc19bb'; Name = 'service.py' },
  [ordered]@{ Local = 'scripts\beta_maintenance.py'; Remote = '/opt/detourvpn-beta/scripts/beta_maintenance.py'; Old = '4d71ab2ff23e2481bc1d535f2c78ef0122b9fcc684f94fde5f3ee58d534eadec'; New = 'a987297b36e2b76e0e5990a856e9663d24f772ffc885840ba6bee87913ab7723'; Name = 'beta_maintenance.py' }
)
$key = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
if (-not (Test-Path -LiteralPath $key -PathType Leaf)) { throw 'Existing beta SSH identity unavailable.' }
foreach ($f in $files) {
  $p = Join-Path $repo $f.Local
  if ((Get-FileHash $p -Algorithm SHA256).Hash.ToLowerInvariant() -ne $f.New) { throw "Local $($f.Name) differs from reviewed version." }
}
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source; $scp = (Get-Command scp.exe -ErrorAction Stop).Source
$opts = @('-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-i',$key)
$remote = "root@$hostIp"; $id = [Guid]::NewGuid().ToString('N'); $stage = "/run/detourvpn-i030-nonce-$id"
$checks = ($files | ForEach-Object { "test `"`$(sha256sum $($_.Remote) | cut -d ' ' -f1)`" = $($_.Old)" }) -join "`n"
$check = @"
set -eu
test "`$(hostname -s)" = $hostname
systemctl is-active --quiet detourvpn-beta-api.service
systemctl is-active --quiet detourvpn-beta-database.service
test "`$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200
$checks
printf 'beta_preflight=passed\n'
"@
$checkOut = @(& $ssh @opts $remote $check)
if ($LASTEXITCODE -ne 0 -or $checkOut -notcontains 'beta_preflight=passed') { throw 'Beta preflight failed (host, health or deployed file differs from expected).' }
Write-Host "host=$hostname; api=active; database=active; readiness=200; sources=expected"
if (-not $Execute) { Write-Host 'mode=preflight_only; rerun with -Execute to deploy'; return }
$bash = @'
set -euo pipefail
s='__STAGE__'; changed=0
declare -A DST=( [service.py]='__R1__' [beta_maintenance.py]='__R2__' )
declare -A OLD=( [service.py]='__O1__' [beta_maintenance.py]='__O2__' )
declare -A NEW=( [service.py]='__N1__' [beta_maintenance.py]='__N2__' )
cleanup() { rm -rf -- "$s"; }
restore() {
  for n in "${!DST[@]}"; do
    f="${DST[$n]}"
    if [ -f "$s/$n.before" ]; then
      install -o "$(stat -c %U "$s/$n.before")" -g "$(stat -c %G "$s/$n.before")" -m "$(stat -c %a "$s/$n.before")" "$s/$n.before" "$f.rollback"
      mv -f -- "$f.rollback" "$f"
    fi
  done
}
rollback() {
  rc=$?
  if [ "$changed" = 1 ]; then restore; echo beta_rollout=rolled_back; echo recovery_copies="$s"; exit "$rc"; fi
  cleanup; exit "$rc"
}
trap rollback ERR
[ "$(hostname -s)" = __HOST__ ]
systemctl is-active --quiet detourvpn-beta-api.service
[ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200 ]
for n in "${!DST[@]}"; do
  [ "$(sha256sum "${DST[$n]}" | cut -d ' ' -f1)" = "${OLD[$n]}" ]
  [ "$(sha256sum "$s/$n" | cut -d ' ' -f1)" = "${NEW[$n]}" ]
  python3 -c 'import ast,pathlib,sys; ast.parse(pathlib.Path(sys.argv[1]).read_text())' "$s/$n"
  cp --preserve=mode,ownership,timestamps -- "${DST[$n]}" "$s/$n.before"
done
changed=1
for n in "${!DST[@]}"; do
  f="${DST[$n]}"
  install -o "$(stat -c %U "$s/$n.before")" -g "$(stat -c %G "$s/$n.before")" -m "$(stat -c %a "$s/$n.before")" "$s/$n" "$f.next"
  mv -f -- "$f.next" "$f"
  [ "$(sha256sum "$f" | cut -d ' ' -f1)" = "${NEW[$n]}" ]
done
systemctl start detourvpn-beta-maintenance.service
[ "$(systemctl show detourvpn-beta-maintenance.service -p Result --value)" = success ]
journalctl -u detourvpn-beta-maintenance.service -n 1 --no-pager -o cat | cut -c1-300
[ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200 ]
changed=0; trap - ERR
echo beta_rollout=success; echo readiness=200
cleanup
'@
$bash = $bash.Replace('__STAGE__',$stage).Replace('__HOST__',$hostname).Replace('__R1__',$files[0].Remote).Replace('__R2__',$files[1].Remote).Replace('__O1__',$files[0].Old).Replace('__O2__',$files[1].Old).Replace('__N1__',$files[0].New).Replace('__N2__',$files[1].New)
$tmp = Join-Path $env:TEMP "detourvpn-i030-nonce-$id.sh"
[IO.File]::WriteAllText($tmp,$bash.Replace("`r`n","`n"),[Text.UTF8Encoding]::new($false))
$deployStarted = $false
try {
  $mk = "set -eu; test ! -e '$stage'; install -d -o root -g root -m 0700 '$stage'; printf 'beta_stage=created\n'"
  $mkOut = @(& $ssh @opts $remote $mk)
  if ($LASTEXITCODE -ne 0 -or $mkOut -notcontains 'beta_stage=created') { throw 'Could not create unique /run stage.' }
  # The host may reset rapid new SSH connections; retry each upload before giving up.
  function Send-Stage([string]$Source, [string]$Name) {
    for ($attempt = 1; $attempt -le 4; $attempt++) {
      & $scp @opts $Source "${remote}:$stage/$Name"
      if ($LASTEXITCODE -eq 0) { Start-Sleep -Seconds 3; return }
      Start-Sleep -Seconds (5 * $attempt)
    }
    throw "Upload failed: $Name"
  }
  foreach ($f in $files) { Send-Stage (Join-Path $repo $f.Local) $f.Name }
  Send-Stage $tmp 'deploy.sh'
  $deployStarted = $true
  $out = @(& $ssh @opts $remote "chmod 0700 '$stage/deploy.sh' && bash '$stage/deploy.sh'"); $code = $LASTEXITCODE
  $out | ForEach-Object { Write-Host $_ }
  if ($code -ne 0 -or $out -notcontains 'beta_rollout=success') { throw 'Deployment not confirmed; do not run a backup.' }
  $receipt = [ordered]@{ issue='I-030'; related='I-027'; timestamp_utc=[DateTime]::UtcNow.ToString('o'); target='isolated beta control plane'; hostname=$hostname; changed_files=@('services/api/src/vpn_api/nodes/service.py','scripts/beta_maintenance.py'); readiness_http=200; migrations='none'; api_restart='none'; maintenance_unit_run='success'; result='success' }
  $path = Join-Path $repo ("docs\evidence\i-030-nonce-cleanup-deploy-{0}.json" -f [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
  [IO.File]::WriteAllText($path,($receipt | ConvertTo-Json),[Text.UTF8Encoding]::new($false)); Write-Host "sanitized_receipt=$path"
} catch {
  # Nothing was installed unless deploy.sh ran and reported; remove only our unique stage if it still holds uploads.
  if (-not $deployStarted) {
  & $ssh @opts $remote "case '$stage' in /run/detourvpn-i030-nonce-*) test -d '$stage' && ! test -e '$stage/deploy.done' && rm -rf -- '$stage';; esac" 2>$null | Out-Null
  }
  throw
} finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
