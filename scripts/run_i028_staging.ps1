# Only existing staging A and bounded I-028 actions; never production/root SSH.
param(
    [ValidateSet('Build', 'Stage', 'Install', 'Status', 'Finish', 'Probe', 'Test')][string]$Action = 'Stage',
    [string]$ClientDirectory = '',
    [ValidateSet('Main', 'Lifecycle', 'NetworkRevoke', 'NaturalSleep')][string]$Suite = 'Main',
    [switch]$RestartClosed,
    [switch]$RunHere
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSsh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', '/home/ad/.ssh/vpn_staging_20261019')
$taskHost = 'vpn-deploy@45.10.166.106'
$taskReadyPath = Join-Path $taskRoot '.cache\i028-ready.json'
function Get-I028Inputs {
    $taskInputs = @('apps/client/integration_test/i028_live_test.dart', 'scripts/i028_pipe_relay.py',
        'scripts/i028_device_scenarios.py',
        'services/api/tests/test_i028_live.py', 'infra/staging-v3/runtime.py',
        'infra/staging-v3/install.sh', 'infra/staging-v3/tls_relay.py',
        'infra/staging-v3/archive_closed.py',
        'services/node-agent/window_daemon.py', 'services/node-agent/window_gate.py',
        'services/node-agent/window_peer.py', 'apps/client/android/app/build.gradle.kts',
        'services/node-agent/window-requirements.txt', 'services/api/src/vpn_api/tunnels/keys.py',
        'services/api/src/vpn_api/tunnels/v3_contracts.py', 'services/api/src/vpn_api/tunnels/window_journal.py',
        'services/api/src/vpn_api/tunnels/window_runtime.py',
        'apps/client/android/app/src/main/AndroidManifest.xml', 'apps/client/pubspec.lock',
        'apps/android/app/build.gradle.kts', 'apps/android/tunnel/build.gradle.kts',
        'apps/android/settings.gradle.kts', 'apps/android/build.gradle.kts',
        'apps/android/gradle.properties', 'apps/android/gradle/wrapper/gradle-wrapper.jar',
        'apps/android/gradle/wrapper/gradle-wrapper.properties',
        'apps/android/app/gradle.lockfile', 'apps/android/tunnel/gradle.lockfile')
    $taskInputs += @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'apps/client/lib'),
        (Join-Path $taskRoot 'apps/client/test'),
        (Join-Path $taskRoot 'apps/client/android/app/src/main/java'),
        (Join-Path $taskRoot 'apps/client/android/app/src/androidTest'),
        (Join-Path $taskRoot 'apps/android/app/src/androidTest'),
        (Join-Path $taskRoot 'apps/android/app/src/main') -File -Recurse | ForEach-Object {
            [IO.Path]::GetRelativePath($taskRoot, $_.FullName).Replace('\','/')
        })
    $taskResult = [ordered]@{}
    foreach ($taskInput in $taskInputs | Sort-Object -Unique) {
        $taskResult[$taskInput] = (Get-FileHash -LiteralPath (Join-Path $taskRoot $taskInput)).Hash
    }
    return $taskResult
}
if ($Action -eq 'Build') {
    if (!$ClientDirectory) {
        $taskCandidates = @(Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'ProjectVpn/i013-builds') -Directory |
            Sort-Object LastWriteTime -Descending | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'apps/client/.dart_tool/package_config.json') })
        if (!$taskCandidates.Count) { throw 'Prepare the reviewed ASCII Flutter snapshot first.' }
        $ClientDirectory = Join-Path $taskCandidates[0].FullName 'apps/client'
    }
    $taskClient = (Resolve-Path -LiteralPath $ClientDirectory).Path
    $taskSnapshotRoot = [IO.Path]::GetFullPath((Join-Path $taskClient '../..'))
    $taskNativeAndroid = Join-Path $taskSnapshotRoot 'apps/android'
    foreach ($taskNativeFile in @('app/build.gradle.kts', 'tunnel/build.gradle.kts',
        'settings.gradle.kts', 'build.gradle.kts', 'gradle.properties',
        'gradle/wrapper/gradle-wrapper.jar', 'gradle/wrapper/gradle-wrapper.properties',
        'app/gradle.lockfile', 'tunnel/gradle.lockfile')) {
        $taskTarget = Join-Path $taskNativeAndroid $taskNativeFile
        New-Item -ItemType Directory -Path (Split-Path -Parent $taskTarget) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $taskRoot ('apps/android/' + $taskNativeFile)) -Destination $taskTarget -Force
    }
    Copy-Item -LiteralPath (Join-Path $taskRoot 'apps/android/app/src/androidTest') -Destination (Join-Path $taskNativeAndroid 'app/src') -Recurse -Force
    [IO.File]::WriteAllText((Join-Path $taskNativeAndroid 'local.properties'), ('sdk.dir=' + (Join-Path $env:LOCALAPPDATA 'Android/Sdk').Replace('\','/') + "`n"), [Text.UTF8Encoding]::new($false))
    foreach ($taskInput in (Get-I028Inputs).Keys) {
        if ($taskInput.StartsWith('apps/client/lib/') -or $taskInput.StartsWith('apps/client/android/app/src/main/') -or
            $taskInput.StartsWith('apps/android/app/src/main/') -or $taskInput.EndsWith('pubspec.lock') -or $taskInput.EndsWith('build.gradle.kts')) {
            if ((Get-FileHash -LiteralPath (Join-Path $taskRoot $taskInput)).Hash -ne
                (Get-FileHash -LiteralPath (Join-Path $taskSnapshotRoot $taskInput)).Hash) { throw 'Reviewed app snapshot is stale; preserve it and prepare an explicit source copy.' }
        }
    }
    Copy-Item -LiteralPath (Join-Path $taskRoot 'apps/client/integration_test/i028_live_test.dart') -Destination (Join-Path $taskClient 'integration_test/i028_live_test.dart') -Force
    Copy-Item -LiteralPath (Join-Path $taskRoot 'apps/client/android/app/src/androidTest') -Destination (Join-Path $taskClient 'android/app/src') -Recurse -Force
    $taskSavedJava = $env:JAVA_HOME
    $taskSavedPub = $env:PUB_CACHE
    try {
        $env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
        $env:PUB_CACHE = Join-Path $env:LOCALAPPDATA 'ProjectVpn/toolchains/pub-cache'
        $taskFlutter = Join-Path $env:LOCALAPPDATA 'ProjectVpn/toolchains/flutter/bin/flutter.bat'
        $taskPin = Get-Content -LiteralPath (Join-Path $taskRoot 'apps/client/toolchain.json') | ConvertFrom-Json
        if ((& git -C (Split-Path -Parent (Split-Path -Parent $taskFlutter)) rev-parse HEAD).Trim() -ne $taskPin.revision) { throw 'Flutter revision mismatch.' }
        Push-Location $taskClient
        try {
            & $taskFlutter pub get --offline --enforce-lockfile
            if ($LASTEXITCODE -ne 0) { throw 'Locked dependencies unavailable.' }
            & $taskFlutter analyze --no-pub
            if ($LASTEXITCODE -ne 0) { throw 'Flutter analysis failed.' }
            & $taskFlutter test --no-pub
            if ($LASTEXITCODE -ne 0) { throw 'Flutter unit checks failed.' }
            foreach ($taskPhase in @('seed', 'run', 'lifecycle', 'network_revoke', 'natural_sleep', 'cleanup')) {
                & $taskFlutter build apk --debug --no-pub --target integration_test/i028_live_test.dart --target-platform android-arm64 --dart-define=VPN_WINDOW_TUNNELS=true "--dart-define=VPN_I028_PHASE=$taskPhase"
                if ($LASTEXITCODE -ne 0) { throw 'Physical entrypoint compilation failed.' }
            }
            Push-Location $taskNativeAndroid
            try {
                & "$env:JAVA_HOME/bin/java.exe" -classpath gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --no-daemon '-Pi028Probe=true' :app:assembleDebugAndroidTest
                if ($LASTEXITCODE -ne 0) { throw 'Independent UID probe compilation failed.' }
            } finally { Pop-Location }
        } finally { Pop-Location }
        @{ client = $taskClient; probe_apk = (Join-Path $taskNativeAndroid 'app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk'); inputs = (Get-I028Inputs) } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $taskReadyPath
    } finally { $env:JAVA_HOME = $taskSavedJava; $env:PUB_CACHE = $taskSavedPub }
    return
}
if ($Action -in @('Install', 'Probe', 'Test')) {
    if (!(Test-Path -LiteralPath $taskReadyPath)) { throw 'Build/verify the complete physical runner before starting the 30-minute timer.' }
    $taskReady = Get-Content -LiteralPath $taskReadyPath | ConvertFrom-Json
    foreach ($taskEntry in (Get-I028Inputs).GetEnumerator()) {
        if ($taskReady.inputs.($taskEntry.Key) -ne $taskEntry.Value) { throw 'Sources changed after the physical runner build.' }
    }
}
if ($Action -eq 'Install') {
    if (!$RunHere) {
        # Visible only because the owner must control the sudo/password terminal.
        $taskLaunch = @('-NoExit', '-File', ('"' + $PSCommandPath + '"'), '-Action', 'Install', '-RunHere')
        if ($RestartClosed) { $taskLaunch += '-RestartClosed' }
        Start-Process (Get-Command pwsh.exe).Source -ArgumentList $taskLaunch -WindowStyle Normal
        return
    }
    Write-Host 'I-028 staging A only. Enter the vpn-deploy sudo password locally; never in chat.'
    Write-Host 'Two closed slots; 30-minute cleanup timer; B API/database and Telegram stay unchanged.'
    $taskInstallCommand = 'sudo /bin/bash /home/vpn-deploy/detourvpn-i028-stage/infra/staging-v3/install.sh'
    if ($RestartClosed) { $taskInstallCommand += ' --archive-closed' }
    wsl -d kali-linux -- ssh -tt @taskSsh $taskHost $taskInstallCommand
    if ($LASTEXITCODE -ne 0) { throw 'I-028 preparation failed. Inspect fixed diagnostics; do not retry blindly.' }
    return
}
if ($Action -in @('Status', 'Finish')) {
    $taskVerb = if ($Action -eq 'Status') { 'status' } else { 'stop' }
    wsl -d kali-linux -- ssh @taskSsh $taskHost "sudo -n /usr/local/libexec/detourvpn-i028-control $taskVerb"
    if ($LASTEXITCODE -ne 0) { throw 'Scoped helper unavailable or cleanup unconfirmed.' }
    return
}
if ($Action -eq 'Probe') {
    $taskAdb = Join-Path $env:LOCALAPPDATA 'Android/Sdk/platform-tools/adb.exe'
    $taskDevices = @(& $taskAdb devices | Where-Object { $_ -match '\tdevice\s*$' })
    if ($taskDevices.Count -ne 1) { throw 'Connect/unlock exactly one authorized physical Android.' }
    $taskSerial = ($taskDevices[0] -split '\s+')[0]
    $taskPackages = & $taskAdb -s $taskSerial shell pm list packages org.example.vpnpoc
    if ($LASTEXITCODE -ne 0 -or $taskPackages -notcontains 'package:org.example.vpnpoc') { throw 'Preserve the existing POC; probe target is missing.' }
    if ($taskPackages -contains 'package:org.example.vpnpoc.test') { throw 'Existing POC test APK preserved; inspect first.' }
    $taskInstalled = $false
    $taskProbeReport = @{ measured_at = [DateTimeOffset]::UtcNow.ToString('o'); scope = 'physical phone-only independent UID diagnostics; no server setup'; vpn_dataplane_acceptance = $false; passed = $false; facts = @{}; test_apk_removed = $false }
    try {
        $taskInstallOutput = & $taskAdb -s $taskSerial install -r $taskReady.probe_apk 2>&1
        if ($LASTEXITCODE -ne 0) { throw 'Probe APK installation failed.' }
        $taskInstalled = $true
        $taskRaw = (& $taskAdb -s $taskSerial shell am instrument -w -r -e mode all org.example.vpnpoc.test/org.example.vpnpoc.I028Probe 2>&1 | Out-String)
        $taskParser = 'import sys,json;sys.path[:0]=["services/api/src","services/api/tests"];from test_i028_live import parse_probe;print(json.dumps(parse_probe(sys.stdin.read())))'
        Push-Location $taskRoot
        try { $taskParsed = $taskRaw | & uv run --frozen python -c $taskParser 2>&1; $taskParseExit = $LASTEXITCODE } finally { Pop-Location }
        $taskRaw = $null
        if ($taskParseExit -ne 0) { throw 'Probe incomplete; raw diagnostic output suppressed.' }
        $taskFacts = ($taskParsed -join "`n") | ConvertFrom-Json
        $taskProbeReport.facts = $taskFacts
        $taskProbeReport.passed = $taskFacts.probe_complete -and $taskFacts.application_initialized -and $taskFacts.process_uid_matches_target
        $taskProbeReport.direct_network_ready = $taskFacts.ipv4_available -and $taskFacts.fixed_udp_dns_response -and !$taskFacts.active_network_is_vpn -and !$taskFacts.egress_is_staging
    } finally {
        if ($taskInstalled) {
            $taskRemoveOutput = & $taskAdb -s $taskSerial uninstall org.example.vpnpoc.test 2>&1
            $taskProbeReport.test_apk_removed = $LASTEXITCODE -eq 0
        }
        $taskProbeReport.passed = $taskProbeReport.passed -and $taskProbeReport.test_apk_removed
        $taskProbeReport | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskRoot 'docs/evidence/i-028-probe-after-startup-fix.json')
    }
    $taskProbeReport | ConvertTo-Json -Depth 4
    if (!$taskProbeReport.passed) { throw 'Phone-only probe failed; inspect the sanitized receipt.' }
    return
}
if ($Action -eq 'Test') {
    $taskAdb = Join-Path $env:LOCALAPPDATA 'Android/Sdk/platform-tools/adb.exe'
    $taskDevices = @(& $taskAdb devices | Where-Object { $_ -match '\tdevice\s*$' })
    if ($taskDevices.Count -ne 1) { throw 'Connect/unlock exactly one physical Android with USB debugging authorized.' }
    $taskOld = @{}
    foreach ($taskName in @('VPN_I028_STAGING_APPROVED', 'VPN_I028_SERIAL', 'VPN_I028_CLIENT_DIR', 'VPN_I028_PROBE_APK', 'VPN_I028_SUITE', 'PYTEST_ADDOPTS')) { $taskOld[$taskName] = [Environment]::GetEnvironmentVariable($taskName) }
    try {
        $env:VPN_I028_STAGING_APPROVED = '45.10.166.106'
        $env:VPN_I028_SERIAL = ($taskDevices[0] -split '\s+')[0]
        $env:VPN_I028_CLIENT_DIR = $taskReady.client
        $env:VPN_I028_PROBE_APK = $taskReady.probe_apk
        $env:VPN_I028_SUITE = $Suite
        $env:PYTEST_ADDOPTS = 'services/api/tests/test_i028_live.py -q --tb=short'
        Push-Location $taskRoot
        try {
            & uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
            if ($LASTEXITCODE -ne 0) { throw 'I-028 physical checks failed; inspect the sanitized evidence receipt.' }
        } finally { Pop-Location }
    } finally {
        foreach ($taskName in $taskOld.Keys) { [Environment]::SetEnvironmentVariable($taskName, $taskOld[$taskName]) }
    }
    return
}
$taskEvidencePath = Join-Path $taskRoot 'docs\evidence\i-028-bootstrap-local.json'
if (!(Test-Path -LiteralPath $taskEvidencePath)) { throw 'Run the isolated bootstrap lab before staging.' }
$taskEvidence = Get-Content -LiteralPath $taskEvidencePath | ConvertFrom-Json
if (!$taskEvidence.passed) { throw 'Bootstrap lab did not pass.' }
$taskText = [IO.File]::ReadAllText((Join-Path $taskRoot 'infra\staging-v3\runtime.py')).Replace("`r`n", "`n")
$taskHasher = [Security.Cryptography.SHA256]::Create()
try { $taskRuntimeHash = [BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskText))).Replace('-', '').ToLowerInvariant() }
finally { $taskHasher.Dispose() }
if ($taskRuntimeHash -ne $taskEvidence.root_runtime_sha256) { throw 'Operator source changed after the real local lab.' }
if (!$taskEvidence.source_hashes) { throw 'Re-run the isolated lab with source binding before staging.' }
foreach ($taskProperty in $taskEvidence.source_hashes.PSObject.Properties) {
    $taskData = [IO.File]::ReadAllText((Join-Path $taskRoot $taskProperty.Name)).Replace("`r`n", "`n")
    $taskHash = [Security.Cryptography.SHA256]::Create()
    try { $taskDigest = [BitConverter]::ToString($taskHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskData))).Replace('-', '').ToLowerInvariant() }
    finally { $taskHash.Dispose() }
    if ($taskDigest -ne $taskProperty.Value) { throw 'Node/bootstrap dependency changed after the local lab.' }
}
$taskStage = Join-Path $taskRoot ('.cache\i028-stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskStage | Out-Null
$taskFiles = @('infra/staging-v3/install.sh', 'infra/staging-v3/runtime.py', 'infra/staging-v3/tls_relay.py', 'infra/staging-v3/archive_closed.py',
    'services/node-agent/window_daemon.py', 'services/node-agent/window_gate.py', 'services/node-agent/window_peer.py',
    'services/node-agent/window-requirements.txt', 'services/api/src/vpn_api/tunnels/keys.py',
    'services/api/src/vpn_api/tunnels/v3_contracts.py', 'services/api/src/vpn_api/tunnels/window_journal.py',
    'services/api/src/vpn_api/tunnels/window_runtime.py')
$taskManifest = [Collections.Generic.List[string]]::new()
foreach ($taskFile in $taskFiles) {
    $taskSource = Join-Path $taskRoot $taskFile
    if ((Get-Item -LiteralPath $taskSource).LinkType -in @('SymbolicLink', 'Junction')) { throw 'Linked source refused.' }
    $taskTarget = Join-Path $taskStage $taskFile
    New-Item -ItemType Directory -Path (Split-Path -Parent $taskTarget) -Force | Out-Null
    [IO.File]::WriteAllText($taskTarget, [IO.File]::ReadAllText($taskSource).Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
    $taskManifest.Add((Get-FileHash -LiteralPath $taskTarget -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $taskFile)
}
New-Item -ItemType Directory -Path (Join-Path $taskStage 'artifacts') | Out-Null
foreach ($taskName in @('awg', 'amneziawg-go')) {
    $taskTarget = Join-Path $taskStage ('artifacts\' + $taskName)
    Copy-Item -LiteralPath (Join-Path $taskRoot ('.cache\i-010\artifacts\' + $taskName)) -Destination $taskTarget
    $taskManifest.Add((Get-FileHash -LiteralPath $taskTarget -Algorithm SHA256).Hash.ToLowerInvariant() + '  artifacts/' + $taskName)
}
[IO.File]::WriteAllText((Join-Path $taskStage 'release.sha256'), ($taskManifest -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
$taskWslStage = (wsl -d kali-linux -- wslpath -a $taskStage.Replace('\', '/')).Trim()
if ($LASTEXITCODE -ne 0 -or !$taskWslStage) { throw 'WSL path resolution failed.' }
if ($RestartClosed) {
    $taskPrevious = Get-Content -LiteralPath (Join-Path $taskRoot 'docs/evidence/i-028-android-staging.json') | ConvertFrom-Json
    if (!$taskPrevious.independent_unprivileged_remote_postcheck) { throw 'Previous remote cleanup is not confirmed; source restart staging refused.' }
    if (!$taskPrevious.temporary_staging_transport_and_phone_cleanup) {
        # The old operator discarded systemctl's inactive output (exit 3).
        # Permit only this diagnosed reporting failure, with every other
        # component confirmed and a fresh read-only timer state check.
        $taskCleanup = $taskPrevious.cleanup_steps
        if (!$taskCleanup -or !$taskCleanup.agent_revoke_and_stop -or !$taskCleanup.local_api_stopped -or !$taskCleanup.phone_restored -or !$taskCleanup.phone_session_absent -or !$taskCleanup.ssh_transport -or !$taskCleanup.root_stop.journal_preserved -or !$taskCleanup.root_stop.temporary_grant_removed -or $taskCleanup.root_stop.gate_close_errors_before_core_stop -ne 0 -or $taskCleanup.root_stop.timeout_timer_disarmed -ne $false) { throw 'Unclassified previous cleanup failure; source restart staging refused.' }
        $taskTimerState = wsl -d kali-linux -- ssh @taskSsh $taskHost 'systemctl show --property=ActiveState --value detourvpn-i028-timeout.timer'
        if ($LASTEXITCODE -ne 0 -or ($taskTimerState | Out-String).Trim() -ne 'inactive') { throw 'Old timeout timer inactivity is not confirmed; source restart staging refused.' }
    }
    wsl -d kali-linux -- ssh @taskSsh $taskHost 'test -d /home/vpn-deploy/detourvpn-i028-stage && test ! -L /home/vpn-deploy/detourvpn-i028-stage && test -O /home/vpn-deploy/detourvpn-i028-stage'
} else {
    wsl -d kali-linux -- ssh @taskSsh $taskHost 'umask 077; test ! -e /home/vpn-deploy/detourvpn-i028-stage && mkdir /home/vpn-deploy/detourvpn-i028-stage'
}
if ($LASTEXITCODE -ne 0) { throw 'Remote source staging refused; inspect actual state.' }
wsl -d kali-linux -- scp -r @taskSsh "$taskWslStage/." "${taskHost}:/home/vpn-deploy/detourvpn-i028-stage/"
if ($LASTEXITCODE -ne 0) { throw 'Source transfer failed; no privileged action performed.' }
wsl -d kali-linux -- ssh @taskSsh $taskHost 'cd /home/vpn-deploy/detourvpn-i028-stage && sha256sum --check release.sha256 >/dev/null && bash -n infra/staging-v3/install.sh'
if ($LASTEXITCODE -ne 0) { throw 'Remote manifest/syntax check failed; no privileged action performed.' }
Write-Output 'I-028 source/artifacts staged and checksummed; no privileged action performed.'
