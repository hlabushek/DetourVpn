[CmdletBinding()]
param([switch]$Execute)
# I-030: publish the reviewed static admin panel (beta invite grant/revoke controls) to the
# isolated beta API host. Static files are read from disk per request: no API restart, no
# migration, no database change. Preflight is read-only; -Execute replaces the three files
# atomically and rolls back to the previous copies if any check fails.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = Split-Path -Parent $PSScriptRoot
$hostIp = '193.68.88.239'; $hostname = 'hiplet-137728'
$remoteDir = '/opt/detourvpn-beta/apps/admin/static'
$files = @(
  [ordered]@{ Name = 'admin.js';    Old = 'ad221f2d71dcbcf70daf42859a96d1f18608d5f7a5c5736d5b28b4ba891a2fe8'; New = '16ec69e29da6ac73a41e5d31900f0ac22090be76aff3f3c4c6f49725d35f601a' },
  [ordered]@{ Name = 'index.html'; Old = 'bbe0b7ada750e9d6f225888b9589bc4021db28436d3a56d34b85c37e12991061'; New = '776893bf57ba4d2d1341d635276f42030cf0967f6c4c752150cc21d8a886a175' },
  [ordered]@{ Name = 'style.css';  Old = 'e1494e4628ce76421f48f8fe58187aaeb14eb6036995b905b68fbe95e537cb40'; New = '2de19766f9c8bfda0d02c0372ee9c9ddaa02d574855560e416a8bf0e721a978f' }
)
$key = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
if (-not (Test-Path -LiteralPath $key -PathType Leaf)) { throw 'Existing beta SSH identity unavailable.' }
foreach ($f in $files) {
  $p = Join-Path $repo ("apps\admin\static\" + $f.Name)
  if ((Get-FileHash $p -Algorithm SHA256).Hash.ToLowerInvariant() -ne $f.New) { throw "Local $($f.Name) differs from the reviewed version." }
}
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source; $scp = (Get-Command scp.exe -ErrorAction Stop).Source
$opts = @('-o','BatchMode=yes','-o','ConnectTimeout=20','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-i',$key)
$remote = "root@$hostIp"; $id = [Guid]::NewGuid().ToString('N'); $stage = "/run/detourvpn-admin-$id"

# The host resets rapid new SSH connections; retry with growing pauses.
function Invoke-Remote([string]$Command) {
  for ($attempt = 1; $attempt -le 6; $attempt++) {
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = @(& $ssh @opts $remote $Command 2>$null); $code = $LASTEXITCODE } finally { $ErrorActionPreference = $previous }
    if ($code -ne 255) { $script:remoteExit = $code; return $out }
    Start-Sleep -Seconds (8 * $attempt)
  }
  throw 'SSH failed after retries.'
}
function Send-File([string]$Source, [string]$Destination) {
  for ($attempt = 1; $attempt -le 6; $attempt++) {
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $scp @opts $Source "${remote}:$Destination" 2>$null; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $previous }
    if ($code -eq 0) { Start-Sleep -Seconds 3; return }
    Start-Sleep -Seconds (8 * $attempt)
  }
  throw 'Upload failed after retries.'
}

$checks = ($files | ForEach-Object { "test `"`$(sha256sum $remoteDir/$($_.Name) | cut -d ' ' -f1)`" = $($_.Old)" }) -join "`n"
$check = @"
set -eu
test "`$(hostname -s)" = $hostname
systemctl is-active --quiet detourvpn-beta-api.service
test "`$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200
$checks
echo beta_preflight=passed
"@.Replace("`r`n","`n")
$out = Invoke-Remote $check
if ($script:remoteExit -ne 0 -or $out -notcontains 'beta_preflight=passed') { throw 'Beta preflight failed (host, health or deployed files differ from expected).' }
Write-Host "host=$hostname; api=active; readiness=200; panel_files=expected_old_version"
if (-not $Execute) { Write-Host 'mode=preflight_only; rerun with -Execute to deploy'; return }

$bash = @'
set -euo pipefail
s='__STAGE__'; d='__DIR__'; changed=0
declare -A OLD=( [admin.js]='__O1__' [index.html]='__O2__' [style.css]='__O3__' )
declare -A NEW=( [admin.js]='__N1__' [index.html]='__N2__' [style.css]='__N3__' )
cleanup() { rm -rf -- "$s"; }
restore() {
  for n in "${!OLD[@]}"; do
    if [ -f "$s/$n.before" ]; then
      install -o root -g detourvpn-beta -m 0640 "$s/$n.before" "$d/$n.rollback"; mv -f -- "$d/$n.rollback" "$d/$n"
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
[ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200 ]
for n in "${!OLD[@]}"; do
  [ "$(sha256sum "$d/$n" | cut -d ' ' -f1)" = "${OLD[$n]}" ]
  [ "$(stat -c '%U:%G:%a' "$d/$n")" = root:detourvpn-beta:640 ]
  [ "$(sha256sum "$s/$n" | cut -d ' ' -f1)" = "${NEW[$n]}" ]
  cp --preserve=mode,ownership,timestamps -- "$d/$n" "$s/$n.before"
done
changed=1
for n in "${!OLD[@]}"; do
  install -o root -g detourvpn-beta -m 0640 "$s/$n" "$d/$n.next"; mv -f -- "$d/$n.next" "$d/$n"
  [ "$(sha256sum "$d/$n" | cut -d ' ' -f1)" = "${NEW[$n]}" ]
done
[ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/admin/)" = 200 ]
[ "$(curl -sS http://127.0.0.1:28000/admin/ | grep -c 'id="beta-invites"')" = 1 ]
[ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200 ]
changed=0; trap - ERR
echo beta_rollout=success; echo admin_panel=200; echo readiness=200
cleanup
'@
$bash = $bash.Replace('__STAGE__',$stage).Replace('__DIR__',$remoteDir).Replace('__HOST__',$hostname)
for ($i = 0; $i -lt 3; $i++) { $bash = $bash.Replace("__O$($i+1)__",$files[$i].Old).Replace("__N$($i+1)__",$files[$i].New) }
$tmp = Join-Path $env:TEMP "detourvpn-admin-$id.sh"
[IO.File]::WriteAllText($tmp,$bash.Replace("`r`n","`n"),[Text.UTF8Encoding]::new($false))
$deployStarted = $false
try {
  $mk = "set -eu; test ! -e '$stage'; install -d -o root -g root -m 0700 '$stage'; echo stage=created"
  if ((Invoke-Remote $mk) -notcontains 'stage=created') { throw 'Could not create unique /run stage.' }
  foreach ($f in $files) { Send-File (Join-Path $repo ("apps\admin\static\" + $f.Name)) "$stage/$($f.Name)" }
  Send-File $tmp "$stage/deploy.sh"
  $deployStarted = $true
  $out = Invoke-Remote "chmod 0700 '$stage/deploy.sh' && bash '$stage/deploy.sh'"; $code = $script:remoteExit
  $out | ForEach-Object { Write-Host $_ }
  if ($code -ne 0 -or $out -notcontains 'beta_rollout=success') { throw 'Deployment not confirmed.' }
  $receipt = [ordered]@{ issue='I-030'; timestamp_utc=[DateTime]::UtcNow.ToString('o'); target='isolated beta control plane'; hostname=$hostname; changed_files=@('apps/admin/static/admin.js','apps/admin/static/index.html','apps/admin/static/style.css'); admin_route_http=200; beta_invite_section_present=$true; readiness_http=200; api_restart='none'; migrations='none'; database_changes='none'; result='success' }
  $path = Join-Path $repo ("docs\evidence\i-030-admin-panel-deploy-{0}.json" -f [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
  [IO.File]::WriteAllText($path,($receipt | ConvertTo-Json),[Text.UTF8Encoding]::new($false)); Write-Host "sanitized_receipt=$path"
} catch {
  if (-not $deployStarted) {
    try { Invoke-Remote "case '$stage' in /run/detourvpn-admin-*) rm -rf -- '$stage';; esac" | Out-Null } catch { }
  }
  throw
} finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
