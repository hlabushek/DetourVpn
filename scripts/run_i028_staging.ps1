# Only existing staging A and bounded I-028 actions; never production/root SSH.
param(
    [ValidateSet('Build', 'Stage', 'Install', 'Status', 'Finish', 'Probe', 'Test', 'TwoDevicePreflight')][string]$Action = 'Stage',
    [string]$ClientDirectory = '',
    [string]$PreparedAwgDirectory = '',
    [ValidateSet('Main', 'Egress', 'Lifecycle', 'NetworkRevoke', 'NaturalSleep', 'TwoDevices')][string]$Suite = 'Main',
    [switch]$RestartClosed,
    [switch]$RunHere
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSsh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', '-i', '/home/ad/.ssh/vpn_staging_20261019')
$taskHost = 'vpn-deploy@45.10.166.106'
$taskReadyPath = Join-Path $taskRoot '.cache\i028-ready.json'
function Get-I028Inputs {
    $taskInputs = @('apps/client/integration_test/i028_live_test.dart', 'apps/client/integration_test/i028_two_device_test.dart', 'scripts/i028_pipe_relay.py',
        'scripts/i028_device_scenarios.py',
        'services/api/tests/test_i028_live.py', 'services/api/tests/test_i028_two_devices.py',
        'services/api/tests/test_i028_two_device_driver.py', 'scripts/__init__.py',
        'scripts/run_i028_staging.ps1', 'scripts/i028_two_device_preflight.py', 'scripts/i028_two_device_driver.py', 'infra/staging-v3/runtime.py',
        'infra/staging-v3/install.sh', 'infra/staging-v3/tls_relay.py', 'infra/staging-v3/dns_observer.py',
        'infra/staging-v3/archive_closed.py',
        'services/node-agent/window_daemon.py', 'services/node-agent/window_gate.py',
        'services/node-agent/window_peer.py', 'apps/client/android/app/build.gradle.kts',
        'apps/client/android/app/gradle.lockfile',
        'services/node-agent/window-requirements.txt', 'services/api/src/vpn_api/tunnels/keys.py',
        'services/api/src/vpn_api/tunnels/v3_contracts.py', 'services/api/src/vpn_api/tunnels/window_journal.py',
        'services/api/src/vpn_api/tunnels/window_runtime.py',
        'apps/client/android/app/src/main/AndroidManifest.xml', 'apps/client/pubspec.lock',
        'apps/android/app/build.gradle.kts', 'apps/android/tunnel/build.gradle.kts',
        'apps/android/settings.gradle.kts', 'apps/android/build.gradle.kts',
        'apps/android/gradle.properties', 'apps/android/gradle/wrapper/gradle-wrapper.jar',
        'apps/android/gradle/wrapper/gradle-wrapper.properties', 'scripts/build_android_native.sh',
        'apps/android/app/gradle.lockfile', 'apps/android/tunnel/gradle.lockfile')
    $taskInputs += 'apps/android/patches/go-only.patch'
    $taskInputs += @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'apps/client/lib'),
        (Join-Path $taskRoot 'apps/client/test'),
        (Join-Path $taskRoot 'apps/client/android/app/src/main/java'),
        (Join-Path $taskRoot 'apps/client/android/app/src/androidTest'),
        (Join-Path $taskRoot 'apps/android/app/src/androidTest'),
        (Join-Path $taskRoot 'apps/android/app/src/main') -File -Recurse | ForEach-Object {
            $taskRelativePath = [IO.Path]::GetRelativePath($taskRoot, $_.FullName).Replace('\','/')
            # Flutter regenerates this registrant from pinned plugins during build; it is not checked-in source.
            if ($taskRelativePath -ne 'apps/client/android/app/src/main/java/io/flutter/plugins/GeneratedPluginRegistrant.java') {
                $taskRelativePath
            }
        })
    $taskResult = [ordered]@{}
    foreach ($taskInput in $taskInputs | Sort-Object -Unique) {
        $taskResult[$taskInput] = (Get-FileHash -LiteralPath (Join-Path $taskRoot $taskInput)).Hash
    }
    return $taskResult
}
function ConvertTo-I028WslPath([string]$WindowsPath) {
    $taskFullPath = [IO.Path]::GetFullPath($WindowsPath)
    if ($taskFullPath -notmatch '^([A-Za-z]):\\(.*)$') { throw 'Native builder paths must be on a mounted Windows drive.' }
    return '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2].Replace('\','/')
}
function Invoke-I028NativeBuild {
    $taskWslRoot = ConvertTo-I028WslPath $taskRoot
    $taskWslSdk = ConvertTo-I028WslPath $PreparedAwgDirectory
    $taskNativeOutput = @(& wsl -d kali-linux -- env "VPN_ANDROID_SDK_ROOT=$taskWslSdk" bash "$taskWslRoot/scripts/build_android_native.sh" 2>&1)
    $taskNativeExit = $LASTEXITCODE
    foreach ($taskNativeLine in $taskNativeOutput) { Write-Host ([string]$taskNativeLine) }
    if ($taskNativeExit -ne 0) { throw 'Pinned Linux native backend build failed.' }
    $taskHashes = [ordered]@{}
    foreach ($taskAbi in @('arm64-v8a', 'x86_64')) {
        $taskLibrary = Join-Path $PreparedAwgDirectory "jniLibs/$taskAbi/libwg-go.so"
        if (!(Test-Path -LiteralPath $taskLibrary)) { throw "Pinned native backend missing for $taskAbi." }
        $taskHashes[$taskAbi] = (Get-FileHash -LiteralPath $taskLibrary -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $taskHashes
}
function Assert-I028ApkNativeBackend([string]$ApkPath, [string]$PackageName) {
    if (!(Test-Path -LiteralPath $ApkPath)) { throw 'Expected Android APK missing before physical acceptance.' }
    $taskVerifier = Join-Path $env:LOCALAPPDATA 'Android/Sdk/build-tools/36.0.0/aapt.exe'
    if (!(Test-Path -LiteralPath $taskVerifier)) { throw 'Pinned Android APK verifier missing.' }
    $taskBadging = & $taskVerifier dump badging $ApkPath
    if ($LASTEXITCODE -ne 0 -or !($taskBadging | Where-Object { $_ -match ("^package: name='" + [regex]::Escape($PackageName) + "'") })) {
        throw 'Native backend APK package identity mismatch.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $taskZip = [IO.Compression.ZipFile]::OpenRead($ApkPath)
    try {
        $taskEntry = $taskZip.GetEntry('lib/arm64-v8a/libwg-go.so')
        if (!$taskEntry) { throw 'Built APK omits the arm64 AmneziaWG backend.' }
        $taskEntryStream = $taskEntry.Open()
        $taskHasher = [Security.Cryptography.SHA256]::Create()
        try { $taskEntryHash = [BitConverter]::ToString($taskHasher.ComputeHash($taskEntryStream)).Replace('-', '').ToLowerInvariant() }
        finally { $taskEntryStream.Dispose(); $taskHasher.Dispose() }
    } finally { $taskZip.Dispose() }
    return $taskEntryHash
}
if ($Action -eq 'Build') {
    if (!$PreparedAwgDirectory) {
        $PreparedAwgDirectory = Join-Path $taskRoot '.cache\i-029-final3\sdk-go-only'
    }
    $PreparedAwgDirectory = (Resolve-Path -LiteralPath $PreparedAwgDirectory).Path
    $taskPreparedReceiptPath = Join-Path $PreparedAwgDirectory 'PREPARED.json'
    if (!(Test-Path -LiteralPath $taskPreparedReceiptPath)) { throw 'Prepared Android tunnel SDK receipt missing.' }
    $taskPreparedReceipt = Get-Content -LiteralPath $taskPreparedReceiptPath | ConvertFrom-Json
    $taskPatchHash = (Get-FileHash -LiteralPath (Join-Path $taskRoot 'apps/android/patches/go-only.patch')).Hash.ToLowerInvariant()
    if ($taskPreparedReceipt.patch_sha256 -ne $taskPatchHash) { throw 'Prepared Android tunnel SDK does not match the reviewed patch.' }
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
            $taskInput.StartsWith('apps/android/app/src/main/') -or $taskInput.EndsWith('pubspec.lock') -or $taskInput.EndsWith('build.gradle.kts') -or $taskInput.EndsWith('gradle.lockfile')) {
            if ((Get-FileHash -LiteralPath (Join-Path $taskRoot $taskInput)).Hash -ne
                (Get-FileHash -LiteralPath (Join-Path $taskSnapshotRoot $taskInput)).Hash) { throw 'Reviewed app snapshot is stale; preserve it and prepare an explicit source copy.' }
        }
    }
    Copy-Item -LiteralPath (Join-Path $taskRoot 'apps/client/integration_test/i028_live_test.dart') -Destination (Join-Path $taskClient 'integration_test/i028_live_test.dart') -Force
    Copy-Item -LiteralPath (Join-Path $taskRoot 'apps/client/integration_test/i028_two_device_test.dart') -Destination (Join-Path $taskClient 'integration_test/i028_two_device_test.dart') -Force
    Copy-Item -LiteralPath (Join-Path $taskRoot 'apps/client/android/app/src/androidTest') -Destination (Join-Path $taskClient 'android/app/src') -Recurse -Force
    $taskSavedJava = $env:JAVA_HOME
    $taskSavedPub = $env:PUB_CACHE
    $taskSavedI028Package = $env:ORG_GRADLE_PROJECT_i028IsolatedPackage
    $taskSavedPreparedAwg = $env:ORG_GRADLE_PROJECT_awgPreparedDir
    try {
        $env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
        $env:PUB_CACHE = Join-Path $env:LOCALAPPDATA 'ProjectVpn/toolchains/pub-cache'
        $env:ORG_GRADLE_PROJECT_i028IsolatedPackage = 'true'
        $env:ORG_GRADLE_PROJECT_awgPreparedDir = $PreparedAwgDirectory
        $taskClientPackage = 'com.detourvpn.client.i028'
        $taskProbePackage = if ($Suite -eq 'TwoDevices') { 'org.example.vpnpoc.i028' } else { 'org.example.vpnpoc' }
        $taskNativeHashes = Invoke-I028NativeBuild
        $taskFlutter = Join-Path $env:LOCALAPPDATA 'ProjectVpn/toolchains/flutter/bin/flutter.bat'
        $taskPin = Get-Content -LiteralPath (Join-Path $taskRoot 'apps/client/toolchain.json') | ConvertFrom-Json
        if ((& git -C (Split-Path -Parent (Split-Path -Parent $taskFlutter)) rev-parse HEAD).Trim() -ne $taskPin.revision) { throw 'Flutter revision mismatch.' }
        Push-Location $taskClient
        try {
            & $taskFlutter pub get --offline --enforce-lockfile
            if ($LASTEXITCODE -ne 0) { throw 'Locked dependencies unavailable.' }
            $taskClientLocalProperties = Join-Path $taskClient 'android/local.properties'
            $taskClientLocalText = if (Test-Path -LiteralPath $taskClientLocalProperties) {
                [IO.File]::ReadAllText($taskClientLocalProperties)
            } else { '' }
            $taskPreparedProperty = 'awg.prepared.dir=' + $PreparedAwgDirectory.Replace('\','/')
            $taskPreparedPropertyPattern = '(?m)^[ \t]*awg\.prepared\.dir[ \t]*[=:].*$'
            if ([regex]::IsMatch($taskClientLocalText, $taskPreparedPropertyPattern)) {
                $taskClientLocalText = [regex]::Replace($taskClientLocalText, $taskPreparedPropertyPattern, $taskPreparedProperty)
            } else {
                if ($taskClientLocalText -and !$taskClientLocalText.EndsWith("`n")) { $taskClientLocalText += "`n" }
                $taskClientLocalText += $taskPreparedProperty + "`n"
            }
            [IO.File]::WriteAllText($taskClientLocalProperties, $taskClientLocalText, [Text.UTF8Encoding]::new($false))
            & $taskFlutter analyze --no-pub
            if ($LASTEXITCODE -ne 0) { throw 'Flutter analysis failed.' }
            & $taskFlutter test --no-pub
            if ($LASTEXITCODE -ne 0) { throw 'Flutter unit checks failed.' }
            $taskPhases = if ($Suite -eq 'Egress') { @('seed', 'egress', 'cleanup') } elseif ($Suite -eq 'NaturalSleep') { @('seed', 'natural_sleep', 'cleanup') } elseif ($Suite -eq 'TwoDevices') { @('seed_run', 'cleanup') } else { @('seed', 'run', 'lifecycle', 'network_revoke', 'natural_sleep', 'cleanup') }
            foreach ($taskPhase in $taskPhases) {
                $taskIntegration = if ($Suite -eq 'TwoDevices') { 'integration_test/i028_two_device_test.dart' } else { 'integration_test/i028_live_test.dart' }
                $taskPhaseDefine = if ($Suite -eq 'TwoDevices') { "--dart-define=VPN_I028_TWO_PHASE=$taskPhase" } else { "--dart-define=VPN_I028_PHASE=$taskPhase" }
                & $taskFlutter build apk --debug --no-pub --target $taskIntegration --target-platform android-arm64 --dart-define=VPN_WINDOW_TUNNELS=true $taskPhaseDefine "--android-project-arg=awgPreparedDir=$PreparedAwgDirectory" '--android-project-arg=i028IsolatedPackage=true'
                if ($LASTEXITCODE -ne 0) { throw 'Physical entrypoint compilation failed.' }
            }
            $taskNaturalApp = $null
            $taskNaturalTest = $null
            $taskNaturalAppHash = $null
            $taskNaturalTestHash = $null
            if ($Suite -eq 'NaturalSleep') {
                $taskDefines = @('VPN_WINDOW_TUNNELS=true', 'VPN_I028_PHASE=natural_sleep') | ForEach-Object { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_)) }
                Push-Location (Join-Path $taskClient 'android')
                try {
                    & .\gradlew.bat --offline :app:assembleDebug :app:assembleDebugAndroidTest '-Pi028FlutterInstrumentation=true' '-Pi028IsolatedPackage=true' '-Ptarget=integration_test/i028_live_test.dart' ('-Pdart-defines=' + ($taskDefines -join ',')) (('-PawgPreparedDir=' + $PreparedAwgDirectory))
                    if ($LASTEXITCODE -ne 0) { throw 'On-device NaturalSleep instrumentation build failed.' }
                } finally { Pop-Location }
                $taskBuiltApp = Join-Path $taskClient 'build/app/outputs/apk/debug/app-debug.apk'
                $taskBuiltTest = Join-Path $taskClient 'build/app/outputs/apk/androidTest/debug/app-debug-androidTest.apk'
                if (!(Test-Path -LiteralPath $taskBuiltApp) -or !(Test-Path -LiteralPath $taskBuiltTest)) { throw 'NaturalSleep instrumentation APKs missing.' }
                # Flutter seed rebuilds app-debug.apk during Test. Retain immutable,
                # content-addressed copies before that phase can overwrite it.
                $taskNaturalAppHash = (Get-FileHash -LiteralPath $taskBuiltApp -Algorithm SHA256).Hash.ToLowerInvariant()
                $taskNaturalTestHash = (Get-FileHash -LiteralPath $taskBuiltTest -Algorithm SHA256).Hash.ToLowerInvariant()
                $taskNaturalApp = Join-Path $taskRoot ('.cache/i028-natural-app-' + $taskNaturalAppHash + '.apk')
                $taskNaturalTest = Join-Path $taskRoot ('.cache/i028-natural-test-' + $taskNaturalTestHash + '.apk')
                if (!(Test-Path -LiteralPath $taskNaturalApp)) { Copy-Item -LiteralPath $taskBuiltApp -Destination $taskNaturalApp }
                if (!(Test-Path -LiteralPath $taskNaturalTest)) { Copy-Item -LiteralPath $taskBuiltTest -Destination $taskNaturalTest }
                if ((Get-FileHash -LiteralPath $taskNaturalApp).Hash.ToLowerInvariant() -ne $taskNaturalAppHash -or (Get-FileHash -LiteralPath $taskNaturalTest).Hash.ToLowerInvariant() -ne $taskNaturalTestHash) { throw 'NaturalSleep immutable APK hash mismatch.' }
            }
            $taskProbeTestPackage = $taskProbePackage + '.test'
            $taskProbeGradleArgs = @('-Pi028Probe=true')
            if ($Suite -eq 'TwoDevices') { $taskProbeGradleArgs += '-Pi028ProbeIsolated=true' }
            Push-Location $taskNativeAndroid
            try {
                & "$env:JAVA_HOME/bin/java.exe" -classpath gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --no-daemon @taskProbeGradleArgs (('-PawgPreparedDir=' + $PreparedAwgDirectory)) :app:assembleDebug :app:assembleDebugAndroidTest
                if ($LASTEXITCODE -ne 0) { throw 'Independent UID probe compilation failed.' }
            } finally { Pop-Location }
        } finally { Pop-Location }
        $taskFinalAppApk = Join-Path $taskClient 'build/app/outputs/flutter-apk/app-debug.apk'
        $taskAapt = Join-Path $env:LOCALAPPDATA 'Android/Sdk/build-tools/36.0.0/aapt.exe'
        if (!(Test-Path -LiteralPath $taskAapt) -or !(Test-Path -LiteralPath $taskFinalAppApk)) { throw 'Isolated I-028 APK or aapt verifier missing.' }
        $taskPackageLine = & $taskAapt dump badging $taskFinalAppApk | Where-Object { $_ -match "^package: name='" }
        if ($LASTEXITCODE -ne 0 -or $taskPackageLine -notmatch "^package: name='org\.example\.vpnclient\.i028'") { throw 'Built APK is not the isolated I-028 package; refusing physical runner readiness.' }
        $taskProbeBaseApk = Join-Path $taskNativeAndroid 'app/build/outputs/apk/debug/app-debug.apk'
        $taskProbeTestApk = Join-Path $taskNativeAndroid 'app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk'
        if (!(Test-Path -LiteralPath $taskProbeBaseApk) -or !(Test-Path -LiteralPath $taskProbeTestApk)) { throw 'Independent UID probe APK pair missing.' }
        $taskBasePackageLine = & $taskAapt dump badging $taskProbeBaseApk | Where-Object { $_ -match "^package: name='" }
        $taskProbeTestPackageLine = & $taskAapt dump badging $taskProbeTestApk | Where-Object { $_ -match "^package: name='" }
        if ($taskBasePackageLine -notmatch ("^package: name='" + [regex]::Escape($taskProbePackage) + "'") -or $taskProbeTestPackageLine -notmatch ("^package: name='" + [regex]::Escape($taskProbeTestPackage) + "'")) { throw 'Independent UID probe APK identity mismatch.' }
        $taskProbeApkNativeHash = Assert-I028ApkNativeBackend $taskProbeBaseApk $taskProbePackage
        $taskProbeBaseHash = (Get-FileHash -LiteralPath $taskProbeBaseApk -Algorithm SHA256).Hash.ToLowerInvariant()
        $taskProbeTestHash = (Get-FileHash -LiteralPath $taskProbeTestApk -Algorithm SHA256).Hash.ToLowerInvariant()
        $taskImmutableProbeBase = Join-Path $taskRoot ('.cache/i028-probe-base-' + $taskProbeBaseHash + '.apk')
        $taskImmutableProbeTest = Join-Path $taskRoot ('.cache/i028-probe-test-' + $taskProbeTestHash + '.apk')
        if (!(Test-Path -LiteralPath $taskImmutableProbeBase)) { Copy-Item -LiteralPath $taskProbeBaseApk -Destination $taskImmutableProbeBase }
        if (!(Test-Path -LiteralPath $taskImmutableProbeTest)) { Copy-Item -LiteralPath $taskProbeTestApk -Destination $taskImmutableProbeTest }
        if ((Get-FileHash -LiteralPath $taskImmutableProbeBase -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskProbeBaseHash -or (Get-FileHash -LiteralPath $taskImmutableProbeTest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskProbeTestHash) { throw 'Immutable probe APK hash mismatch.' }
        $taskFinalAppApk = Join-Path $taskClient 'build/app/outputs/flutter-apk/app-debug.apk'
        $taskClientApkNativeHash = Assert-I028ApkNativeBackend $taskFinalAppApk $taskClientPackage
        if ($taskClientApkNativeHash -cne $taskProbeApkNativeHash) { throw 'Client and probe APKs package different native backends.' }
        $taskClientApkHash = (Get-FileHash -LiteralPath $taskFinalAppApk -Algorithm SHA256).Hash.ToLowerInvariant()
        $taskImmutableClient = Join-Path $taskRoot ('.cache/i028-client-' + $taskClientApkHash + '.apk')
        if (!(Test-Path -LiteralPath $taskImmutableClient)) { Copy-Item -LiteralPath $taskFinalAppApk -Destination $taskImmutableClient }
        if ((Get-FileHash -LiteralPath $taskImmutableClient -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskClientApkHash) { throw 'Immutable client APK hash mismatch.' }
        @{ suite = $Suite; client = $taskClient; client_package = $taskClientPackage; client_apk = $taskImmutableClient; client_apk_sha256 = $taskClientApkHash; client_native_sha256 = $taskNativeHashes; apk_native_sha256 = $taskClientApkNativeHash; probe_package = $taskProbePackage; probe_native_sha256 = $taskNativeHashes; probe_test_package = $taskProbeTestPackage; probe_apk = $taskImmutableProbeTest; probe_base_apk = $taskImmutableProbeBase; probe_base_sha256 = $taskProbeBaseHash; probe_test_sha256 = $taskProbeTestHash; natural_app_apk = $taskNaturalApp; natural_app_sha256 = $taskNaturalAppHash; natural_test_apk = $taskNaturalTest; prepared_awg_path = $PreparedAwgDirectory; prepared_awg_patch_sha256 = $taskPatchHash; inputs = (Get-I028Inputs) } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $taskReadyPath
    } finally {
        $env:JAVA_HOME = $taskSavedJava
        $env:PUB_CACHE = $taskSavedPub
        $env:ORG_GRADLE_PROJECT_i028IsolatedPackage = $taskSavedI028Package
        $env:ORG_GRADLE_PROJECT_awgPreparedDir = $taskSavedPreparedAwg
    }
    return
}
if ($Action -in @('Install', 'Probe', 'Test')) {
    if (!(Test-Path -LiteralPath $taskReadyPath)) { throw 'Build/verify the complete physical runner before starting the 30-minute timer.' }
    $taskReady = Get-Content -LiteralPath $taskReadyPath | ConvertFrom-Json
    if ($Action -in @('Install', 'Test') -and (($Suite -eq 'Egress') -ne ($taskReady.suite -eq 'Egress'))) { throw 'Egress diagnostic and other suites require their own matching Build.' }
    if ($Action -in @('Install', 'Test') -and $Suite -eq 'TwoDevices' -and $taskReady.suite -ne 'TwoDevices') { throw 'Build with -Suite TwoDevices before installing or running the dual-device acceptance.' }
    if (!$taskReady.prepared_awg_path -or !$taskReady.prepared_awg_patch_sha256) { throw 'Prepared tunnel SDK binding missing; rebuild the source-bound runner.' }
    $taskReadyPatchHash = (Get-FileHash -LiteralPath (Join-Path $taskRoot 'apps/android/patches/go-only.patch')).Hash.ToLowerInvariant()
    if ($taskReady.prepared_awg_patch_sha256 -ne $taskReadyPatchHash -or !(Test-Path -LiteralPath $taskReady.prepared_awg_path)) { throw 'Prepared tunnel SDK binding is stale; rebuild the source-bound runner.' }
    foreach ($taskEntry in (Get-I028Inputs).GetEnumerator()) {
        if ($taskReady.inputs.($taskEntry.Key) -ne $taskEntry.Value) { throw 'Sources changed after the physical runner build.' }
    }
    if ($Action -eq 'Test') {
        if ($Suite -eq 'TwoDevices') {
            $taskClientLocalProperties = Join-Path $taskReady.client 'android/local.properties'
            if (!(Test-Path -LiteralPath $taskClientLocalProperties)) { throw 'Generated Android local.properties is missing; rebuild the physical runner.' }
            $taskPreparedMatch = [regex]::Match([IO.File]::ReadAllText($taskClientLocalProperties), '(?m)^[ \t]*awg\.prepared\.dir[ \t]*[=:](.*)$')
            if (!$taskPreparedMatch.Success -or $taskPreparedMatch.Groups[1].Value.Trim() -ine ([string]$taskReady.prepared_awg_path).Replace('\','/')) { throw 'Generated Android SDK binding is stale; rebuild the physical runner.' }
        }
        $taskClientGradleProperties = Join-Path $taskReady.client 'android/gradle.properties'
        $taskClientGradlePropertiesExisted = Test-Path -LiteralPath $taskClientGradleProperties
        $taskClientGradlePropertiesOriginal = if ($taskClientGradlePropertiesExisted) { [IO.File]::ReadAllBytes($taskClientGradleProperties) } else { $null }
    }
    if ($Action -eq 'Test') {
        if (!(Test-Path -LiteralPath $taskReady.client_apk) -or (Get-FileHash -LiteralPath $taskReady.client_apk -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskReady.client_apk_sha256) {
            throw 'Source-bound client APK changed after Build; rebuild the physical runner.'
        }
        $taskClientApkNativeHash = Assert-I028ApkNativeBackend $taskReady.client_apk $taskReady.client_package
        if ($taskClientApkNativeHash -cne $taskReady.apk_native_sha256) { throw 'Client APK native backend changed after Build.' }
        foreach ($taskAbi in @('arm64-v8a', 'x86_64')) {
            $taskLibrary = Join-Path $taskReady.prepared_awg_path "jniLibs/$taskAbi/libwg-go.so"
            if (!(Test-Path -LiteralPath $taskLibrary) -or (Get-FileHash -LiteralPath $taskLibrary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskReady.client_native_sha256.$taskAbi) {
                throw "Prepared native backend changed after Build for $taskAbi."
            }
        }
        if ($Suite -eq 'TwoDevices') {
            if (!(Test-Path -LiteralPath $taskReady.probe_base_apk) -or (Get-FileHash -LiteralPath $taskReady.probe_base_apk -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskReady.probe_base_sha256) {
                throw 'Immutable physical probe APK changed after Build.'
            }
            $taskProbeApkNativeHash = Assert-I028ApkNativeBackend $taskReady.probe_base_apk $taskReady.probe_package
            if ($taskProbeApkNativeHash -cne $taskReady.apk_native_sha256) { throw 'Probe APK native backend differs from the source-bound client APK.' }
        }
    }
    if ($Action -eq 'Test' -and $Suite -eq 'NaturalSleep') {
        if (!(Test-Path -LiteralPath $taskReady.natural_app_apk) -or !(Test-Path -LiteralPath $taskReady.natural_test_apk)) { throw 'Build NaturalSleep instrumentation before Test.' }
        if ((Get-FileHash -LiteralPath $taskReady.natural_app_apk).Hash.ToLowerInvariant() -ne $taskReady.natural_app_sha256 -or (Get-FileHash -LiteralPath $taskReady.natural_test_apk).Hash.ToLowerInvariant() -ne $taskReady.natural_test_sha256) { throw 'NaturalSleep instrumentation APK changed after Build.' }
    }
}
if ($Action -eq 'TwoDevicePreflight') {
    if (!(Test-Path -LiteralPath $taskReadyPath)) { throw 'Build/verify the source-bound I-028 runner first.' }
    $taskReady = Get-Content -LiteralPath $taskReadyPath | ConvertFrom-Json
    if ($taskReady.suite -ne 'TwoDevices') { throw 'Build with -Suite TwoDevices before the two-device preflight.' }
    foreach ($taskEntry in (Get-I028Inputs).GetEnumerator()) {
        if ($taskReady.inputs.($taskEntry.Key) -ne $taskEntry.Value) { throw 'Sources changed after the I-028 runner build; rebuild first.' }
    }
    if (!$taskReady.probe_base_apk -or !$taskReady.probe_test_sha256) { throw 'Rebuild the probe APK pair before the two-device preflight.' }
    Push-Location $taskRoot
    try { & uv run --frozen python scripts/i028_two_device_preflight.py --ready .cache/i028-ready.json }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Two-device read-only preflight failed; no device or staging changes were made.' }
    return
}
if ($Action -eq 'Install') {
    if ($Suite -eq 'TwoDevices') {
        Push-Location $taskRoot
        try { & uv run --frozen python scripts/i028_two_device_preflight.py --ready .cache/i028-ready.json }
        finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw 'Fresh read-only two-device preflight failed; staging install was not started.' }
        $taskTimerState = wsl -d kali-linux -- ssh @taskSsh $taskHost 'systemctl show --property=ActiveState --value detourvpn-i028-timeout.timer'
        if ($LASTEXITCODE -ne 0 -or ($taskTimerState | Out-String).Trim() -ne 'inactive') { throw 'Read-only staging A cleanup-timer check did not confirm inactive; staging install was not started.' }
    }
    if (!$RunHere) {
        # Visible only because the owner must control the sudo/password terminal.
        $taskLaunch = @('-NoExit', '-File', ('"' + $PSCommandPath + '"'), '-Action', 'Install', '-Suite', $Suite, '-RunHere')
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
    if ($Action -eq 'Status' -and !$RunHere) {
        $taskLaunch = @('-NoExit', '-File', ('"' + $PSCommandPath + '"'), '-Action', 'Status', '-RunHere')
        Start-Process (Get-Command pwsh.exe).Source -ArgumentList $taskLaunch -WindowStyle Normal
        return
    }
    if ($Action -eq 'Status') {
        wsl -d kali-linux -- ssh -tt @taskSsh $taskHost 'sudo /usr/local/libexec/detourvpn-i028-control status'
    } else {
        wsl -d kali-linux -- ssh @taskSsh $taskHost 'sudo -n /usr/local/libexec/detourvpn-i028-control stop'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Scoped helper unavailable or cleanup unconfirmed.' }
    return
}
if ($Action -eq 'Probe') {
    $taskAdb = Join-Path $env:LOCALAPPDATA 'Android/Sdk/platform-tools/adb.exe'
    $taskDevices = @(& $taskAdb devices | Where-Object { $_ -match '\tdevice\s*$' })
    if ($taskDevices.Count -ne 1) { throw 'Connect/unlock exactly one authorized physical Android.' }
    $taskSerial = ($taskDevices[0] -split '\s+')[0]
    $taskProbePackage = if ($taskReady.probe_package) { [string]$taskReady.probe_package } else { 'org.example.vpnpoc' }
    $taskProbeTestPackage = if ($taskReady.probe_test_package) { [string]$taskReady.probe_test_package } else { 'org.example.vpnpoc.test' }
    $taskInstalledPackages = @(& $taskAdb -s $taskSerial shell pm list packages $taskProbePackage)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the probe package inventory.' }
    $taskBaseWasInstalled = $taskInstalledPackages -contains "package:$taskProbePackage"
    $taskTestWasInstalled = $taskInstalledPackages -contains "package:$taskProbeTestPackage"
    if ($taskTestWasInstalled) { throw 'Existing probe instrumentation APK preserved; inspect first.' }
    if (!$taskBaseWasInstalled -and !$taskReady.probe_base_apk) { throw 'Probe base APK is unavailable; preserve the existing app.' }
    $taskBaseInstalledByProbe = $false
    $taskTestInstalledByProbe = $false
    $taskProbeReport = @{ measured_at = [DateTimeOffset]::UtcNow.ToString('o'); scope = 'physical phone-only independent UID diagnostics; no server setup'; vpn_dataplane_acceptance = $false; passed = $false; facts = @{}; test_apk_removed = $false; probe_base_removed = $true }
    try {
        if (!$taskBaseWasInstalled) {
            $taskBaseInstallOutput = & $taskAdb -s $taskSerial install $taskReady.probe_base_apk 2>&1
            if ($LASTEXITCODE -ne 0) { throw 'Probe base APK installation failed.' }
            $taskBaseInstalledByProbe = $true
        }
        $taskInstallOutput = & $taskAdb -s $taskSerial install -r $taskReady.probe_apk 2>&1
        if ($LASTEXITCODE -ne 0) { throw 'Probe APK installation failed.' }
        $taskTestInstalledByProbe = $true
        $taskRaw = (& $taskAdb -s $taskSerial shell am instrument -w -r -e mode all "$taskProbeTestPackage/org.example.vpnpoc.I028Probe" 2>&1 | Out-String)
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
        if ($taskTestInstalledByProbe) {
            $taskRemoveOutput = & $taskAdb -s $taskSerial uninstall $taskProbeTestPackage 2>&1
            $taskProbeReport.test_apk_removed = $LASTEXITCODE -eq 0
        }
        if ($taskBaseInstalledByProbe) {
            $taskRemoveBaseOutput = & $taskAdb -s $taskSerial uninstall $taskProbePackage 2>&1
            $taskProbeReport.probe_base_removed = $LASTEXITCODE -eq 0
        }
        $taskProbeReport.passed = $taskProbeReport.passed -and $taskProbeReport.test_apk_removed -and $taskProbeReport.probe_base_removed
        $taskProbeReport.direct_ipv6_ready = $taskProbeReport.facts.ipv6_available -and $taskProbeReport.facts.ipv6_literal_connected -and $taskProbeReport.facts.ipv6_literal_egress_port_control_connected -and !$taskProbeReport.facts.active_network_is_vpn -and !$taskProbeReport.facts.egress_is_staging
        $taskProbeReport.passed = $taskProbeReport.passed -and $taskProbeReport.direct_ipv6_ready
        $taskProbeEvidenceName = 'i-028-probe-after-startup-fix-' + [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '.json'
        $taskProbeEvidencePath = Join-Path $taskRoot ('docs/evidence/' + $taskProbeEvidenceName)
        $taskProbeReport.evidence_file = 'docs/evidence/' + $taskProbeEvidenceName
        $taskProbeReport | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $taskProbeEvidencePath
    }
    $taskProbeReport | ConvertTo-Json -Depth 4
    if (!$taskProbeReport.passed) { throw 'Phone-only probe failed; inspect the sanitized receipt.' }
    return
}
if ($Action -eq 'Test') {
    $taskAdb = Join-Path $env:LOCALAPPDATA 'Android/Sdk/platform-tools/adb.exe'
    $taskDevices = @(& $taskAdb devices -l | Where-Object { $_ -match '^\S+\s+device(?:\s|$)' -and $_ -notmatch '^emulator-' })
    if ($Suite -eq 'TwoDevices') {
        if ($taskDevices.Count -ne 2) { throw 'Connect exactly two authorized physical Android devices.' }
        Push-Location $taskRoot
        try { & uv run --frozen python scripts/i028_two_device_preflight.py --ready .cache/i028-ready.json }
        finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw 'Fresh read-only two-device preflight failed; no installation or staging action was started.' }
    } elseif ($taskDevices.Count -ne 1) {
        throw 'Connect/unlock exactly one physical Android with USB debugging authorized.'
    }
    $taskOld = @{}
    foreach ($taskName in @('VPN_I028_STAGING_APPROVED', 'VPN_I028_SERIAL', 'VPN_I028_CLIENT_DIR', 'VPN_I028_PROBE_APK', 'VPN_I028_NATURAL_APP_APK', 'VPN_I028_NATURAL_TEST_APK', 'VPN_I028_SUITE', 'VPN_I028_PREPARED_AWG', 'PYTEST_ADDOPTS', 'ORG_GRADLE_PROJECT_i028IsolatedPackage', 'ORG_GRADLE_PROJECT_i028FlutterInstrumentation', 'ORG_GRADLE_PROJECT_awgPreparedDir')) { $taskOld[$taskName] = [Environment]::GetEnvironmentVariable($taskName) }
    try {
        $env:VPN_I028_STAGING_APPROVED = '45.10.166.106'
        if ($Suite -ne 'TwoDevices') {
            $env:VPN_I028_SERIAL = ($taskDevices[0] -split '\s+')[0]
        }
        $env:VPN_I028_CLIENT_DIR = $taskReady.client
        $env:VPN_I028_PROBE_APK = $taskReady.probe_apk
        $env:VPN_I028_NATURAL_APP_APK = $taskReady.natural_app_apk
        $env:VPN_I028_NATURAL_TEST_APK = $taskReady.natural_test_apk
        $env:VPN_I028_SUITE = $Suite
        $env:VPN_I028_PREPARED_AWG = $taskReady.prepared_awg_path
        $env:PYTEST_ADDOPTS = if ($Suite -eq 'TwoDevices') { 'services/api/tests/test_i028_two_devices.py -q --tb=short' } else { 'services/api/tests/test_i028_live.py -q --tb=short' }
        $env:ORG_GRADLE_PROJECT_awgPreparedDir = $taskReady.prepared_awg_path
        # `flutter test` rejects --android-project-arg. Put the required Gradle
        # properties in the isolated snapshot for every Flutter-triggered rebuild.
        $taskPropertiesText = if ($taskClientGradlePropertiesExisted) { [IO.File]::ReadAllText($taskClientGradleProperties) } else { '' }
        $taskPropertyNames = @('i028IsolatedPackage')
        if ($Suite -in @('TwoDevices', 'NaturalSleep')) { $taskPropertyNames += 'i028FlutterInstrumentation' }
        foreach ($taskPropertyName in $taskPropertyNames) {
                $taskPropertyPattern = '(?m)^[ \t]*(?:org\.gradle\.project\.)?' + [regex]::Escape($taskPropertyName) + '[ \t]*=[ \t]*(\S+)[ \t]*$'
                $taskExistingProperty = [regex]::Matches($taskPropertiesText, $taskPropertyPattern)
                foreach ($taskProperty in $taskExistingProperty) {
                    if ($taskProperty.Groups[1].Value -cne 'true') { throw "Conflicting I-028 Gradle property: $taskPropertyName" }
                }
                $taskPropertiesText = [regex]::Replace($taskPropertiesText, $taskPropertyPattern, '')
        }
        $taskPropertiesText = $taskPropertiesText.TrimEnd() + "`n" + (($taskPropertyNames | ForEach-Object { "$_=true" }) -join "`n") + "`n"
        [IO.File]::WriteAllText($taskClientGradleProperties, $taskPropertiesText, [Text.UTF8Encoding]::new($false))
        [Environment]::SetEnvironmentVariable('ORG_GRADLE_PROJECT_i028IsolatedPackage', $null)
        [Environment]::SetEnvironmentVariable('ORG_GRADLE_PROJECT_i028FlutterInstrumentation', $null)

        # A daemon started before these temporary project properties can
        # retain the old model. Stop it before pytest launches flutter test.
        Push-Location (Join-Path $taskReady.client 'android')
        try {
            # gradlew.bat requires JAVA_HOME, which may be unset in the caller's shell.
            $taskGradleJava = Join-Path $env:ProgramFiles 'Android/Android Studio/jbr/bin/java.exe'
            if (!(Test-Path -LiteralPath $taskGradleJava)) { throw 'Pinned Android Studio Java runtime missing.' }
            & $taskGradleJava -classpath gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --stop
            if ($LASTEXITCODE -ne 0) { throw 'Could not restart the isolated Gradle daemon before I-028 test.' }
        } finally { Pop-Location }

        if ($Suite -eq 'TwoDevices') {
            $taskFlutter = Join-Path $env:LOCALAPPDATA 'ProjectVpn/toolchains/flutter/bin/flutter.bat'
            Push-Location $taskReady.client
            try {
                & $taskFlutter build apk --debug --no-pub --target integration_test/i028_two_device_test.dart --target-platform android-arm64 --dart-define=VPN_WINDOW_TUNNELS=true ("--android-project-arg=awgPreparedDir=" + [string]$taskReady.prepared_awg_path)
                $taskIsolatedBuildExit = $LASTEXITCODE
            } finally { Pop-Location }
            if ($taskIsolatedBuildExit -ne 0) { throw 'Pre-install I-028 app build failed; no physical install was started.' }
            $taskPreflightApk = Join-Path $taskReady.client 'build/app/outputs/flutter-apk/app-debug.apk'
            $taskPreflightNativeHash = Assert-I028ApkNativeBackend $taskPreflightApk 'com.detourvpn.client.i028'
            if ($taskPreflightNativeHash -cne [string]$taskReady.apk_native_sha256) { throw 'Pre-install I-028 APK backend differs from the source-bound ready artifact.' }
        Write-Host 'I028_TEST_APP_PACKAGE=com.detourvpn.client.i028'
        }
        Push-Location $taskRoot
        try {
            & uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
            if ($LASTEXITCODE -ne 0) { throw 'I-028 physical checks failed; inspect the sanitized evidence receipt.' }
        } finally { Pop-Location }
    } finally {
        if ($taskClientGradleProperties) {
            if ($taskClientGradlePropertiesExisted) {
                [IO.File]::WriteAllBytes($taskClientGradleProperties, $taskClientGradlePropertiesOriginal)
            } elseif (Test-Path -LiteralPath $taskClientGradleProperties) {
                Remove-Item -LiteralPath $taskClientGradleProperties
            }
        }
        foreach ($taskName in $taskOld.Keys) { [Environment]::SetEnvironmentVariable($taskName, $taskOld[$taskName]) }
    }
    return
}
$taskEvidencePath = Join-Path $taskRoot 'docs\evidence\i-028-bootstrap-local-dns-observer.json'
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
$taskFiles = @('infra/staging-v3/install.sh', 'infra/staging-v3/runtime.py', 'infra/staging-v3/tls_relay.py', 'infra/staging-v3/archive_closed.py', 'infra/staging-v3/dns_observer.py',
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
    if ($Suite -eq 'Egress') {
        $taskLatestEgress = Get-ChildItem -LiteralPath (Join-Path $taskRoot 'docs/evidence') -Filter 'i-028-egress-diagnostic*.json' | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if (!$taskLatestEgress) { throw 'Previous Egress cleanup evidence missing; source restart staging refused.' }
        $taskPrevious = Get-Content -LiteralPath $taskLatestEgress.FullName | ConvertFrom-Json
    } else {
        $taskPrevious = Get-Content -LiteralPath (Join-Path $taskRoot 'docs/evidence/i-028-android-staging.json') | ConvertFrom-Json
    }
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
wsl -d kali-linux -- ssh @taskSsh $taskHost 'test -d /home/vpn-deploy/detourvpn-i028-stage && test ! -L /home/vpn-deploy/detourvpn-i028-stage && chmod -R go-rwx /home/vpn-deploy/detourvpn-i028-stage && ! find /home/vpn-deploy/detourvpn-i028-stage -perm /077 -print -quit 2>&1 | grep -q .'
if ($LASTEXITCODE -ne 0) { throw 'Staged source permissions are not private; no privileged action performed.' }
wsl -d kali-linux -- ssh @taskSsh $taskHost 'cd /home/vpn-deploy/detourvpn-i028-stage && sha256sum --check release.sha256 >/dev/null && bash -n infra/staging-v3/install.sh'
if ($LASTEXITCODE -ne 0) { throw 'Remote manifest/syntax check failed; no privileged action performed.' }
Write-Output 'I-028 source/artifacts staged and checksummed; no privileged action performed.'
