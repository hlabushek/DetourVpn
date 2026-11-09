[CmdletBinding()]
param([switch]$Execute)
# I-030/I-027: second-host copy of the encrypted beta backup (no new accounts, no decryption key).
#  - node: unprivileged user + hardened daily timer that pulls the archive and keeps 7 copies (about 7 days, ADR-090);
#  - API host: one dedicated SSH key restricted to a single read-only `cat` of the archive,
#    and the daily archive timer (until now the archive existed only when the PC asked for it).
# Preflight is read-only. -Execute changes both hosts and rolls the API host back on failure.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = Split-Path -Parent $PSScriptRoot
$apiIp = '193.68.88.239'; $apiHostname = 'hiplet-137728'
$nodeIp = '95.81.73.183'
$key = Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026'
$knownHostsFile = Join-Path $env:USERPROFILE '.ssh\known_hosts'
if (-not (Test-Path -LiteralPath $key -PathType Leaf)) { throw 'Existing beta SSH identity unavailable.' }
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source; $scp = (Get-Command scp.exe -ErrorAction Stop).Source
$opts = @('-o','BatchMode=yes','-o','ConnectTimeout=20','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-i',$key)

# The hosts reset rapid new SSH connections; retry with growing pauses.
function Invoke-Remote([string]$Ip, [string]$Command) {
  for ($attempt = 1; $attempt -le 6; $attempt++) {
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = @(& $ssh @opts "root@$Ip" $Command 2>$null); $code = $LASTEXITCODE } finally { $ErrorActionPreference = $previous }
    if ($code -ne 255) { $script:remoteExit = $code; return $out }
    Start-Sleep -Seconds (8 * $attempt)
  }
  throw "SSH to $Ip failed after retries."
}
function Send-File([string]$Ip, [string]$Source, [string]$Destination) {
  for ($attempt = 1; $attempt -le 6; $attempt++) {
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $scp @opts $Source "root@${Ip}:$Destination" 2>$null; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $previous }
    if ($code -eq 0) { Start-Sleep -Seconds 3; return }
    Start-Sleep -Seconds (8 * $attempt)
  }
  throw "Upload to $Ip failed after retries."
}

$files = [ordered]@{
  script = Join-Path $repo 'scripts\beta_backup_mirror.py'
  service = Join-Path $repo 'infra\beta-node\detourvpn-backup-mirror.service'
  timer = Join-Path $repo 'infra\beta-node\detourvpn-backup-mirror.timer'
  apiTimer = Join-Path $repo 'infra\beta\detourvpn-beta-backup.timer'
}
foreach ($f in $files.Values) { if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { throw "Missing $f" } }
$hash = @{}; foreach ($k in $files.Keys) { $hash[$k] = (Get-FileHash $files[$k] -Algorithm SHA256).Hash.ToLowerInvariant() }

# ---- read-only preflight ----
$apiCheck = @'
set -eu
test "$(hostname -s)" = __API_HOSTNAME__
systemctl is-active --quiet detourvpn-beta-api.service
test -d /var/lib/detourvpn-beta-backups
test -f /etc/systemd/system/detourvpn-beta-backup.service
test -f /var/lib/detourvpn-beta-backups/latest.enc
test -f /root/.ssh/authorized_keys
if grep -q detourvpn-backup-mirror /root/.ssh/authorized_keys; then echo mirror_key=present; else echo mirror_key=absent; fi
if [ -e /etc/systemd/system/detourvpn-beta-backup.timer ]; then echo api_timer=present; else echo api_timer=absent; fi
echo api_preflight=passed
'@.Replace('__API_HOSTNAME__', $apiHostname).Replace("`r`n","`n")
$a = Invoke-Remote $apiIp $apiCheck
if ($script:remoteExit -ne 0 -or $a -notcontains 'api_preflight=passed') { throw 'API host preflight failed.' }
$nodeCheck = @'
set -eu
test "$(hostname -s)" != __API_HOSTNAME__
systemctl cat detourvpn-beta-node-agent.service >/dev/null
command -v python3 >/dev/null; command -v ssh >/dev/null; command -v ssh-keygen >/dev/null
python3 -c 'import sys; assert sys.version_info >= (3, 10)'
if id -u detourvpn-backup-mirror >/dev/null 2>&1; then echo mirror_user=present; else echo mirror_user=absent; fi
echo node_free_kib="$(df -Pk /var/lib | awk 'NR==2{print $4}')"
echo node_preflight=passed
'@.Replace('__API_HOSTNAME__', $apiHostname).Replace("`r`n","`n")
$n = Invoke-Remote $nodeIp $nodeCheck
if ($script:remoteExit -ne 0 -or $n -notcontains 'node_preflight=passed') { throw 'Node host preflight failed.' }
$a | Where-Object { $_ -match '^(mirror_key|api_timer)=' } | ForEach-Object { Write-Host $_ }
$n | Where-Object { $_ -match '^(mirror_user|node_free_kib)=' } | ForEach-Object { Write-Host $_ }
Write-Host 'preflight=passed'
if (-not $Execute) { Write-Host 'mode=preflight_only; rerun with -Execute to install'; return }

# ---- execute ----
$apiKnown = @(Get-Content -LiteralPath $knownHostsFile | Where-Object { $_ -match "^$([regex]::Escape($apiIp)) ssh-ed25519 [0-9A-Za-z+/=]+$" })
if ($apiKnown.Count -ne 1) { throw 'Expected exactly one pinned ed25519 host key for the API host in known_hosts.' }
$id = [Guid]::NewGuid().ToString('N')
$nodeStage = "/run/detourvpn-mirror-$id"; $apiStage = "/run/detourvpn-mirror-$id"
$knownLocal = Join-Path $env:TEMP "detourvpn-mirror-known-$id"
[IO.File]::WriteAllText($knownLocal, ($apiKnown[0] + "`n"), [Text.UTF8Encoding]::new($false))

$nodeInstall = @'
set -euo pipefail
s='__STAGE__'
for f in script service timer; do test -f "$s/$f"; done
printf '%s  %s\n' '__H_SCRIPT__' "$s/script" | sha256sum -c - >/dev/null
printf '%s  %s\n' '__H_SERVICE__' "$s/service" | sha256sum -c - >/dev/null
printf '%s  %s\n' '__H_TIMER__' "$s/timer" | sha256sum -c - >/dev/null
id -u detourvpn-backup-mirror >/dev/null 2>&1 || useradd --system --home-dir /var/lib/detourvpn-backup-mirror --shell /usr/sbin/nologin --user-group --no-create-home detourvpn-backup-mirror
install -d -o root -g root -m 0755 /opt/detourvpn-backup-mirror
install -d -o detourvpn-backup-mirror -g detourvpn-backup-mirror -m 0700 /var/lib/detourvpn-backup-mirror /var/lib/detourvpn-backup-mirror/archives
install -d -o root -g detourvpn-backup-mirror -m 0750 /etc/detourvpn-backup-mirror
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$s/script"
install -o root -g root -m 0644 "$s/script" /opt/detourvpn-backup-mirror/beta_backup_mirror.py
sed 's/__API_HOST__/__API_IP__/' "$s/service" > "$s/service.rendered"
systemd-analyze verify "$s/service.rendered" "$s/timer" >/dev/null 2>&1 || true
install -o root -g root -m 0644 "$s/service.rendered" /etc/systemd/system/detourvpn-backup-mirror.service
install -o root -g root -m 0644 "$s/timer" /etc/systemd/system/detourvpn-backup-mirror.timer
k=/etc/detourvpn-backup-mirror/id_ed25519
if [ ! -e "$k" ]; then
  ssh-keygen -q -t ed25519 -N '' -C detourvpn-backup-mirror -f "$k"
  chown detourvpn-backup-mirror:detourvpn-backup-mirror "$k" "$k.pub"; chmod 0600 "$k"; chmod 0644 "$k.pub"
fi
systemctl daemon-reload
printf 'pubkey=%s\n' "$(cut -d ' ' -f1,2 "$k.pub")"
echo node_install=done
'@
$nodeInstall = $nodeInstall.Replace('__STAGE__',$nodeStage).Replace('__API_IP__',$apiIp).Replace('__H_SCRIPT__',$hash.script).Replace('__H_SERVICE__',$hash.service).Replace('__H_TIMER__',$hash.timer).Replace("`r`n","`n")

$apiInstall = @'
set -euo pipefail
s='__STAGE__'; ak=/root/.ssh/authorized_keys; before="/root/authorized_keys.pre-mirror-__ID__"
test "$(hostname -s)" = __API_HOSTNAME__
test -f "$s/apiTimer"; printf '%s  %s\n' '__H_TIMER__' "$s/apiTimer" | sha256sum -c - >/dev/null
test ! -e /etc/systemd/system/detourvpn-beta-backup.timer
if grep -q detourvpn-backup-mirror "$ak"; then exit 1; fi
cp -a -- "$ak" "$before"
printf '%s\n' 'restrict,command="/usr/bin/cat -- /var/lib/detourvpn-beta-backups/latest.enc" __PUBKEY__ detourvpn-backup-mirror' >> "$ak"
install -o root -g root -m 0644 "$s/apiTimer" /etc/systemd/system/detourvpn-beta-backup.timer
systemd-analyze verify /etc/systemd/system/detourvpn-beta-backup.timer >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl enable --now detourvpn-beta-backup.timer
systemctl is-enabled --quiet detourvpn-beta-backup.timer
echo api_install=done
'@
$apiRollback = @'
set -eu
before="/root/authorized_keys.pre-mirror-__ID__"
if [ -f "$before" ]; then cp -a -- "$before" /root/.ssh/authorized_keys; fi
systemctl disable --now detourvpn-beta-backup.timer >/dev/null 2>&1 || true
rm -f -- /etc/systemd/system/detourvpn-beta-backup.timer
systemctl daemon-reload
echo api_rolled_back
'@.Replace('__ID__',$id).Replace("`r`n","`n")
$apiInstallDone = $false
try {
  $mk = "set -eu; test ! -e '$nodeStage'; install -d -o root -g root -m 0700 '$nodeStage'; echo stage=ok"
  if ((Invoke-Remote $nodeIp $mk) -notcontains 'stage=ok') { throw 'Node stage failed.' }
  Send-File $nodeIp $files.script "$nodeStage/script"; Send-File $nodeIp $files.service "$nodeStage/service"; Send-File $nodeIp $files.timer "$nodeStage/timer"
  $nodeScriptLocal = Join-Path $env:TEMP "detourvpn-mirror-node-$id.sh"
  [IO.File]::WriteAllText($nodeScriptLocal, $nodeInstall, [Text.UTF8Encoding]::new($false))
  Send-File $nodeIp $nodeScriptLocal "$nodeStage/install.sh"
  $r = Invoke-Remote $nodeIp "chmod 0700 '$nodeStage/install.sh' && bash '$nodeStage/install.sh'"
  if ($script:remoteExit -ne 0 -or $r -notcontains 'node_install=done') { throw 'Node install failed.' }
  $pub = ($r | Where-Object { $_ -match '^pubkey=' } | Select-Object -First 1) -replace '^pubkey=',''
  if ($pub -notmatch '^ssh-ed25519 AAAA[0-9A-Za-z+/=]+$') { throw 'Unexpected public key format.' }

  $mkApi = "set -eu; test ! -e '$apiStage'; install -d -o root -g root -m 0700 '$apiStage'; echo stage=ok"
  if ((Invoke-Remote $apiIp $mkApi) -notcontains 'stage=ok') { throw 'API stage failed.' }
  Send-File $apiIp $files.apiTimer "$apiStage/apiTimer"
  $apiScriptLocal = Join-Path $env:TEMP "detourvpn-mirror-api-$id.sh"
  $apiBody = $apiInstall.Replace('__STAGE__',$apiStage).Replace('__ID__',$id).Replace('__API_HOSTNAME__',$apiHostname).Replace('__H_TIMER__',$hash.apiTimer).Replace('__PUBKEY__',$pub).Replace("`r`n","`n")
  [IO.File]::WriteAllText($apiScriptLocal, $apiBody, [Text.UTF8Encoding]::new($false))
  Send-File $apiIp $apiScriptLocal "$apiStage/install.sh"
  $apiInstallDone = $true
  $r = Invoke-Remote $apiIp "chmod 0700 '$apiStage/install.sh' && bash '$apiStage/install.sh'"
  if ($script:remoteExit -ne 0 -or $r -notcontains 'api_install=done') { throw 'API install failed.' }

  Send-File $nodeIp $knownLocal "$nodeStage/known_hosts"
  $first = "set -euo pipefail; install -o root -g detourvpn-backup-mirror -m 0640 '$nodeStage/known_hosts' /etc/detourvpn-backup-mirror/known_hosts; systemctl enable --now detourvpn-backup-mirror.timer; systemctl start detourvpn-backup-mirror.service; test `"`$(systemctl show detourvpn-backup-mirror.service -p Result --value)`" = success; journalctl -u detourvpn-backup-mirror.service -n 5 --no-pager -o cat | grep -E '^\{' | tail -1 | cut -c1-200; ls -l --time-style=long-iso /var/lib/detourvpn-backup-mirror/archives | tail -3; echo first_pull=ok"
  $r = Invoke-Remote $nodeIp $first
  $r | ForEach-Object { Write-Host $_ }
  if ($script:remoteExit -ne 0 -or $r -notcontains 'first_pull=ok') { throw 'First mirror pull failed.' }
  Write-Host 'mirror_install=success'
  foreach ($pair in @(@($nodeIp,$nodeStage),@($apiIp,$apiStage))) { Invoke-Remote $pair[0] "case '$($pair[1])' in /run/detourvpn-mirror-*) rm -rf -- '$($pair[1])';; esac" | Out-Null }
  $receipt = [ordered]@{ issue='I-030'; related='I-027'; timestamp_utc=[DateTime]::UtcNow.ToString('o'); target='isolated beta API host and beta node'; node_unit='detourvpn-backup-mirror.timer'; api_unit='detourvpn-beta-backup.timer'; retention_copies=7; decryption_key_on_node=$false; ssh_key_restricted_to_single_cat=$true; first_pull='success'; result='success' }
  $path = Join-Path $repo ("docs\evidence\i-027-backup-mirror-install-{0}.json" -f [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
  [IO.File]::WriteAllText($path,($receipt | ConvertTo-Json),[Text.UTF8Encoding]::new($false)); Write-Host "sanitized_receipt=$path"
} catch {
  Write-Host "install_failed: $($_.Exception.Message)"
  if ($apiInstallDone) {
    try { (Invoke-Remote $apiIp $apiRollback) | ForEach-Object { Write-Host $_ } } catch { Write-Host 'API rollback could not run; restore /root/.ssh/authorized_keys from the pre-mirror copy.' }
  }
  try { Invoke-Remote $nodeIp "systemctl disable --now detourvpn-backup-mirror.timer >/dev/null 2>&1 || true" | Out-Null } catch { }
  throw
} finally {
  foreach ($p in @($knownLocal,$nodeScriptLocal,$apiScriptLocal)) { if ($p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue } }
}
