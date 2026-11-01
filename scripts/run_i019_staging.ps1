# Owner-run local preparation; secrets are typed in local PTYs, never stored.
param([switch]$Prepare, [switch]$UseExistingGrants, [switch]$SyntaxCheck, [switch]$BuildOnly,
    [switch]$DiagnosticSession, [switch]$FinishSession)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (@($Prepare, $UseExistingGrants, $SyntaxCheck, $BuildOnly, $FinishSession | Where-Object { $_ }).Count -ne 1) {
    throw 'Choose exactly one of -Prepare, -UseExistingGrants, -SyntaxCheck, -BuildOnly, -FinishSession.'
}
if ($DiagnosticSession -and !($Prepare -or $UseExistingGrants)) {
    throw '-DiagnosticSession requires -Prepare or -UseExistingGrants.'
}
if ($FinishSession) {
    # Does not require a phone, a current APK or a working prior test database.
    Push-Location -LiteralPath $taskRoot
    try {
        & uv run --frozen python -c "import sys; sys.path[:0]=['services/api/tests','services/api/src']; import test_i019_live as live; sys.exit(0 if live.finish_diagnostic_session() else 1)"
        if ($LASTEXITCODE -ne 0) { throw 'Session cleanup is not confirmed on both hosts; inspect fixed cleanup receipt.' }
    } finally { Pop-Location }
    exit 0
}
function Get-I019SourceStamp {
    $taskFiles = @()
    foreach ($taskPart in @('apps/client/lib', 'apps/client/test', 'apps/client/integration_test',
        'apps/client/android/app/src', 'apps/android/app/src', 'services/api/tests')) {
        $taskFiles += Get-ChildItem -LiteralPath (Join-Path $taskRoot $taskPart) -File -Recurse |
            Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -notin @('.pyc', '.pyo') }
    }
    foreach ($taskPart in @('apps/client/pubspec.yaml', 'apps/client/pubspec.lock',
        'apps/client/android/app/build.gradle.kts', 'apps/client/android/app/gradle.lockfile',
        'apps/client/android/build.gradle.kts', 'apps/android/tunnel/build.gradle.kts',
        'apps/android/tunnel/gradle.lockfile', 'scripts/build_flutter_client.ps1',
        'scripts/run_i019_staging.ps1', 'scripts/run_i028_staging.ps1')) {
        $taskFiles += Get-Item -LiteralPath (Join-Path $taskRoot $taskPart)
    }
    $taskRows = [string[]]@($taskFiles | ForEach-Object {
        $_.FullName.Replace('\', '/').ToLowerInvariant() + ':' +
            (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    })
    # PS5/.NET Framework and PS7/.NET have different culture sorting behavior.
    [Array]::Sort($taskRows, [StringComparer]::Ordinal)
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes(($taskRows -join "`n")))) }
    finally { $taskHasher.Dispose() }
}
function Build-I019IndependentProbe {
    $taskPreparedAwg = Join-Path $taskRoot '.cache\i-029-final3\sdk-go-only'
    $taskReceipt = Join-Path $taskPreparedAwg 'PREPARED.json'
    $taskPatch = Join-Path $taskRoot 'apps/android/patches/go-only.patch'
    if (!(Test-Path -LiteralPath $taskReceipt)) { throw 'Prepared native Android backend receipt missing; no staging grant was requested.' }
    $taskReceiptValue = Get-Content -LiteralPath $taskReceipt | ConvertFrom-Json
    $taskPatchHash = (Get-FileHash -LiteralPath $taskPatch -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($taskReceiptValue.patch_sha256 -ne $taskPatchHash) { throw 'Prepared native Android backend does not match the pinned patch.' }
    $taskAndroid = Join-Path $taskRoot 'apps/android'
    $taskLocal = Join-Path $taskAndroid 'local.properties'
    $taskLocalText = if (Test-Path -LiteralPath $taskLocal) { [IO.File]::ReadAllText($taskLocal) } else { '' }
    $taskSdkProperty = 'sdk.dir=' + (Join-Path $env:LOCALAPPDATA 'Android/Sdk').Replace('\','/')
    if ([regex]::IsMatch($taskLocalText, '(?m)^[ \t]*sdk\.dir[ \t]*[=:].*$')) {
        $taskLocalText = [regex]::Replace($taskLocalText, '(?m)^[ \t]*sdk\.dir[ \t]*[=:].*$', $taskSdkProperty)
    } else {
        if ($taskLocalText -and !$taskLocalText.EndsWith("`n")) { $taskLocalText += "`n" }
        $taskLocalText += $taskSdkProperty + "`n"
    }
    [IO.File]::WriteAllText($taskLocal, $taskLocalText, [Text.UTF8Encoding]::new($false))
    $taskJavaBefore = $env:JAVA_HOME
    try {
        $env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
        Push-Location -LiteralPath $taskAndroid
        try {
            & "$env:JAVA_HOME\bin\java.exe" -classpath gradle/wrapper/gradle-wrapper.jar `
                org.gradle.wrapper.GradleWrapperMain --offline --no-daemon '-Pi028Probe=true' '-Pi028ProbeIsolated=true' `
                ('-PawgPreparedDir=' + $taskPreparedAwg) ':app:assembleDebug' ':app:assembleDebugAndroidTest'
            if ($LASTEXITCODE -ne 0) { throw 'Independent-UID egress probe build failed before staging.' }
        } finally { Pop-Location }
    } finally { $env:JAVA_HOME = $taskJavaBefore }
    $taskBaseApk = Join-Path $taskAndroid 'app/build/outputs/apk/debug/app-debug.apk'
    $taskTestApk = Join-Path $taskAndroid 'app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk'
    $taskAapt = Join-Path $env:LOCALAPPDATA 'Android/Sdk/build-tools/36.0.0/aapt.exe'
    if (!(Test-Path -LiteralPath $taskAapt) -or !(Test-Path -LiteralPath $taskBaseApk) -or !(Test-Path -LiteralPath $taskTestApk)) {
        throw 'Independent-UID probe APK pair is missing.'
    }
    $taskBaseIdentity = (& $taskAapt dump badging $taskBaseApk | Where-Object { $_ -match "^package: name='" })
    $taskTestIdentity = (& $taskAapt dump badging $taskTestApk | Where-Object { $_ -match "^package: name='" })
    if ($LASTEXITCODE -ne 0 -or $taskBaseIdentity -notmatch "^package: name='org\.example\.vpnpoc\.i028'" -or
        $taskTestIdentity -notmatch "^package: name='org\.example\.vpnpoc\.i028\.test'") {
        throw 'Independent-UID probe APK package identity mismatch.'
    }
    $taskBaseHash = (Get-FileHash -LiteralPath $taskBaseApk -Algorithm SHA256).Hash.ToLowerInvariant()
    $taskTestHash = (Get-FileHash -LiteralPath $taskTestApk -Algorithm SHA256).Hash.ToLowerInvariant()
    $taskCache = Join-Path $taskRoot '.cache'
    $taskImmutableBase = Join-Path $taskCache ('i019-probe-base-' + $taskBaseHash + '.apk')
    $taskImmutableTest = Join-Path $taskCache ('i019-probe-test-' + $taskTestHash + '.apk')
    if (!(Test-Path -LiteralPath $taskImmutableBase)) { Copy-Item -LiteralPath $taskBaseApk -Destination $taskImmutableBase }
    if (!(Test-Path -LiteralPath $taskImmutableTest)) { Copy-Item -LiteralPath $taskTestApk -Destination $taskImmutableTest }
    if ((Get-FileHash -LiteralPath $taskImmutableBase).Hash.ToLowerInvariant() -ne $taskBaseHash -or
        (Get-FileHash -LiteralPath $taskImmutableTest).Hash.ToLowerInvariant() -ne $taskTestHash) {
        throw 'Immutable independent-UID probe APK hash mismatch.'
    }
    @{ base_apk = $taskImmutableBase; base_sha256 = $taskBaseHash; test_apk = $taskImmutableTest;
        test_sha256 = $taskTestHash; base_package = 'org.example.vpnpoc.i028';
        test_package = 'org.example.vpnpoc.i028.test' } |
        ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $taskCache 'i019-probe-ready.json')
    Write-Host 'Independent-UID IPv4 egress probe built and hash-pinned.'
}
if ($SyntaxCheck) {
    & wsl -d kali-linux --cd $taskRoot -- /tmp/vpn-ansible-2.21.5/bin/ansible-playbook `
        -i infra/ansible/inventory.i019.yml infra/ansible/staging-i019.yml --syntax-check
    if ($LASTEXITCODE -ne 0) { throw 'I-019 syntax check failed.' }
    exit 0
}
if (!$BuildOnly) {
    $taskAdb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
    $taskDevices = @(& $taskAdb devices | Where-Object { $_ -match '\tdevice\s*$' })
    if ($LASTEXITCODE -ne 0 -or $taskDevices.Count -ne 1) {
        throw 'Connect/unlock exactly one physical Android.'
    }
}
$taskPrivate = Join-Path $env:LOCALAPPDATA 'ProjectVpn\i019-private'
if ($Prepare -or $BuildOnly) {
    $taskDirectory = New-Item -ItemType Directory -Force -Path $taskPrivate
    if ($taskDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Private directory must not be a link.' }
    $taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if ((Get-Acl -LiteralPath $taskPrivate).GetOwner([Security.Principal.SecurityIdentifier]) -ne $taskIdentity) {
        throw 'Private directory must belong to this user.'
    }
    $taskAcl = [Security.AccessControl.DirectorySecurity]::new()
    $taskAcl.SetAccessRuleProtection($true, $false)
    $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $taskIdentity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]$taskDirectory, $taskAcl)
    } else { $taskDirectory.SetAccessControl($taskAcl) }
    foreach ($taskHost in @('45.10.166.106', '150.241.99.227')) {
        $taskTrusted = @(& ssh-keygen -F $taskHost -f (Join-Path $env:USERPROFILE '.ssh\known_hosts') |
            Where-Object { $_ -and !$_.StartsWith('#') })
        if ($LASTEXITCODE -ne 0 -or !$taskTrusted.Count) { throw 'Missing existing trusted staging SSH host key.' }
        $taskTrusted | & wsl -d kali-linux -- /bin/sh -c 'umask 077; cat >> /home/ad/.ssh/known_hosts'
        if ($LASTEXITCODE -ne 0) { throw 'Cannot copy public SSH host trust.' }
    }
    $taskStampPath = Join-Path $taskPrivate 'build-source-stamp'
    $taskPathFile = Join-Path $taskPrivate 'client-path'
    $taskStamp = Get-I019SourceStamp
    $taskCached = (Test-Path -LiteralPath $taskStampPath) -and (Test-Path -LiteralPath $taskPathFile)
    if ($taskCached) {
        $taskCached = [IO.File]::ReadAllText($taskStampPath) -eq $taskStamp
        $taskSnapshotClient = [IO.File]::ReadAllText($taskPathFile)
        $taskCached = $taskCached -and (Test-Path -LiteralPath (Join-Path $taskSnapshotClient 'build/app/outputs/flutter-apk/app-debug.apk'))
    }
    if (!$taskCached) {
    # Build every target before giving any remote grant. Dot sourcing exposes the
    # verified ASCII source snapshot path from the established build runner.
    . (Join-Path $PSScriptRoot 'build_flutter_client.ps1')
    $taskSnapshotClient = Join-Path $taskBuildRoot 'apps\client'
    $taskFlutter = Join-Path $env:LOCALAPPDATA 'ProjectVpn\toolchains\flutter\bin\flutter.bat'
    $taskJavaBefore = $env:JAVA_HOME
    $taskPubBefore = $env:PUB_CACHE
    try {
        $env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
        $env:PUB_CACHE = Join-Path $env:LOCALAPPDATA 'ProjectVpn\toolchains\pub-cache'
        Push-Location -LiteralPath $taskSnapshotClient
        try {
            & $taskFlutter pub get --offline --enforce-lockfile
            if ($LASTEXITCODE -ne 0) { throw 'Pinned physical-test packages unavailable.' }
            & $taskFlutter build apk --debug --no-pub --target-platform android-arm64 `
                --target integration_test/node_failover_live_test.dart
            if ($LASTEXITCODE -ne 0) { throw 'I-019 physical test did not compile; staging unchanged.' }
        } finally { Pop-Location }
    } finally { $env:JAVA_HOME = $taskJavaBefore; $env:PUB_CACHE = $taskPubBefore }
    [IO.File]::WriteAllText((Join-Path $taskPrivate 'client-path'), $taskSnapshotClient)
    } else { Write-Host 'Compiled source snapshot is current; no repeated build needed.' }
    [IO.File]::WriteAllText($taskStampPath, (Get-I019SourceStamp))
    Build-I019IndependentProbe
    if ($BuildOnly) { Write-Host 'Local checks and physical test compilation complete; staging unchanged.'; exit 0 }
    Push-Location -LiteralPath $taskRoot
    try {
        & uv run --frozen python -c "import sys; sys.path[:0]=['services/api/tests','services/api/src']; import test_i019_live as live; print({host: live.clock_bound(host) for host in live.HOSTS})"
        if ($LASTEXITCODE -ne 0) { throw 'Read-only clock preflight failed. No new grants installed; inspect clock synchronization first.' }
    } finally { Pop-Location }
    foreach ($taskNode in @('i019-node-a', 'i019-node-b')) {
        Write-Host "Prepare $taskNode only. Enter its vpn-deploy sudo password locally (hidden input)."
        $taskCommand = 'test -t 0 && test -t 1 || exit 70; exec /tmp/vpn-ansible-2.21.5/bin/ansible-playbook -i infra/ansible/inventory.i019.yml infra/ansible/staging-i019.yml --extra-vars vpn_i019_staging_approved=true --limit ' + $taskNode + ' --ask-become-pass'
        & wsl -d kali-linux --cd $taskRoot -- /usr/bin/script --quiet --return --echo never `
            --command $taskCommand /dev/null
        if ($LASTEXITCODE -ne 0) { throw 'I-019 grant preparation failed; inspect sanitized task/recap, do not retry blindly.' }
    }
    Write-Host 'Both bounded grants prepared; VPN endpoints have not started.'
    if ($DiagnosticSession) {
        Write-Host 'Diagnostic session: failed attempts stop runtimes but retain the original 45-minute grants. No renewal.'
        Write-Host 'Codex can run -UseExistingGrants -DiagnosticSession and finish with -FinishSession. Successful drill revokes grants automatically.'
    }
    exit 0
}
$taskClientPath = Join-Path $taskPrivate 'client-path'
if (!(Test-Path -LiteralPath $taskClientPath)) { throw 'Run -Prepare first.' }
$taskSnapshotClient = [IO.File]::ReadAllText($taskClientPath)
if ([IO.File]::ReadAllText((Join-Path $taskPrivate 'build-source-stamp')) -ne (Get-I019SourceStamp)) {
    throw 'Client sources changed after local checks; run -BuildOnly before staging.'
}
$taskLiveTest = 'integration_test\node_failover_live_test.dart'
if ((Get-FileHash -LiteralPath (Join-Path $taskRoot "apps\client\$taskLiveTest")).Hash -ne
    (Get-FileHash -LiteralPath (Join-Path $taskSnapshotClient $taskLiveTest)).Hash) {
    throw 'Physical test source changed after preparation.'
}
$taskProbeReadyPath = Join-Path $taskRoot '.cache\i019-probe-ready.json'
if (!(Test-Path -LiteralPath $taskProbeReadyPath)) { throw 'Build the independent-UID egress probe before staging.' }
$taskProbeReady = Get-Content -LiteralPath $taskProbeReadyPath | ConvertFrom-Json
if ($taskProbeReady.base_package -ne 'org.example.vpnpoc.i028' -or $taskProbeReady.test_package -ne 'org.example.vpnpoc.i028.test' -or
    !(Test-Path -LiteralPath $taskProbeReady.base_apk) -or !(Test-Path -LiteralPath $taskProbeReady.test_apk) -or
    (Get-FileHash -LiteralPath $taskProbeReady.base_apk).Hash.ToLowerInvariant() -ne $taskProbeReady.base_sha256 -or
    (Get-FileHash -LiteralPath $taskProbeReady.test_apk).Hash.ToLowerInvariant() -ne $taskProbeReady.test_sha256) {
    throw 'Independent-UID probe APK changed after preparation; rebuild before staging.'
}
$taskOldApproval = $env:VPN_I019_STAGING_APPROVED
$taskOldClient = $env:VPN_I019_CLIENT_DIR
$taskOldPytest = $env:PYTEST_ADDOPTS
$taskOldSession = $env:VPN_I019_DIAGNOSTIC_SESSION
$taskOldProbeBase = $env:VPN_I019_PROBE_BASE_APK
$taskOldProbeBaseHash = $env:VPN_I019_PROBE_BASE_SHA256
$taskOldProbeTest = $env:VPN_I019_PROBE_TEST_APK
$taskOldProbeTestHash = $env:VPN_I019_PROBE_TEST_SHA256
try {
    $env:VPN_I019_STAGING_APPROVED = '45.10.166.106,150.241.99.227'
    $env:VPN_I019_CLIENT_DIR = $taskSnapshotClient
    $env:VPN_I019_DIAGNOSTIC_SESSION = if ($DiagnosticSession) { 'true' } else { 'false' }
    $env:VPN_I019_PROBE_BASE_APK = [string]$taskProbeReady.base_apk
    $env:VPN_I019_PROBE_BASE_SHA256 = [string]$taskProbeReady.base_sha256
    $env:VPN_I019_PROBE_TEST_APK = [string]$taskProbeReady.test_apk
    $env:VPN_I019_PROBE_TEST_SHA256 = [string]$taskProbeReady.test_sha256
    $env:PYTEST_ADDOPTS = '-q -s --tb=short -k test_i019_physical_two_host_failover'
    Push-Location -LiteralPath $taskRoot
    try {
        & uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
        if ($LASTEXITCODE -ne 0) {
            throw 'I-019 live drill failed; inspect progress/attempt receipt and verify runtime cleanup before any retry. Diagnostic session retains original grants only until their original deadline.'
        }
    } finally { Pop-Location }
} finally {
    $env:VPN_I019_STAGING_APPROVED = $taskOldApproval
    $env:VPN_I019_CLIENT_DIR = $taskOldClient
    $env:PYTEST_ADDOPTS = $taskOldPytest
    $env:VPN_I019_DIAGNOSTIC_SESSION = $taskOldSession
    $env:VPN_I019_PROBE_BASE_APK = $taskOldProbeBase
    $env:VPN_I019_PROBE_BASE_SHA256 = $taskOldProbeBaseHash
    $env:VPN_I019_PROBE_TEST_APK = $taskOldProbeTest
    $env:VPN_I019_PROBE_TEST_SHA256 = $taskOldProbeTestHash
}
