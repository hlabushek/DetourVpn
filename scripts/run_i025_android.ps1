param([string]$Serial = '', [string]$ClientDirectory = '', [switch]$BuildOnly, [switch]$Session)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskAdb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
$taskDevices = @(& $taskAdb devices | Where-Object { $_ -match '\tdevice\s*$' })
if ($taskDevices.Count -ne 1) { throw 'Connect and unlock exactly one physical Android.' }
$taskDetectedSerial = ($taskDevices[0] -split '\s+')[0]
if ($Serial -and $Serial -ne $taskDetectedSerial) { throw 'Unexpected Android serial.' }
$Serial = $taskDetectedSerial
if (!$Session -and !$BuildOnly -and (& $taskAdb -s $Serial shell pm list packages org.example.vpnclient) -match '^package:org.example.vpnclient') {
    throw 'Test target already installed: preserve account/data, do not clear or uninstall it.'
}
if (!$ClientDirectory) {
    $taskBuildRoot = Join-Path $env:LOCALAPPDATA 'ProjectVpn\i013-builds'
    $taskAvailable = @(Get-ChildItem -LiteralPath $taskBuildRoot -Directory | Sort-Object LastWriteTime -Descending | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName 'apps\client\build\app\outputs\flutter-apk\app-debug.apk')
    })
    if (!$taskAvailable.Count) { throw 'Build a reviewed local snapshot first; see I-025 runbook.' }
    $ClientDirectory = Join-Path $taskAvailable[0].FullName 'apps\client'
}
$taskClient = (Resolve-Path -LiteralPath $ClientDirectory).Path
$taskSourceDirectories = @((Join-Path $taskRoot 'apps\client\lib'), (Join-Path $taskRoot 'apps\client\android\app\src\main\java\org\example\vpnpoc'))
foreach ($taskSource in Get-ChildItem -LiteralPath $taskSourceDirectories -File -Recurse) {
    $taskRelative = [IO.Path]::GetRelativePath((Join-Path $taskRoot 'apps\client'), $taskSource.FullName)
    if ((Get-FileHash -LiteralPath $taskSource.FullName).Hash -ne (Get-FileHash -LiteralPath (Join-Path $taskClient $taskRelative)).Hash) { throw 'Snapshot app source is stale.' }
}
if ($Session) {
    Copy-Item -LiteralPath (Join-Path $taskRoot 'apps\client\integration_test\i025_session_test.dart') -Destination (Join-Path $taskClient 'integration_test\i025_session_test.dart') -Force
    $taskSavedJava = $env:JAVA_HOME
    $taskSavedPub = $env:PUB_CACHE
    $taskSavedOptIn = $env:VPN_I025_LOCAL_ANDROID
    $taskSavedSerial = $env:VPN_I025_SERIAL
    $taskSavedClient = $env:VPN_I025_CLIENT_DIR
    $taskSavedPytest = $env:PYTEST_ADDOPTS
    try {
        $env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
        $env:PUB_CACHE = Join-Path $env:LOCALAPPDATA 'ProjectVpn\toolchains\pub-cache'
        $taskFlutter = Join-Path $env:LOCALAPPDATA 'ProjectVpn\toolchains\flutter\bin\flutter.bat'
        Push-Location $taskClient
        try {
            & $taskFlutter pub get --offline --enforce-lockfile
            if ($LASTEXITCODE -ne 0) { throw 'Locked dependencies unavailable.' }
            & $taskFlutter build apk --debug --no-pub --target integration_test/i025_session_test.dart --target-platform android-arm64
            if ($LASTEXITCODE -ne 0) { throw 'Session target compilation failed; local API not started.' }
        } finally { Pop-Location }
        if ($BuildOnly) { return }
        $env:VPN_I025_LOCAL_ANDROID = '1'
        $env:VPN_I025_SERIAL = $Serial
        $env:VPN_I025_CLIENT_DIR = $taskClient
        $env:PYTEST_ADDOPTS = 'services/api/tests/test_i025_android.py -q'
        Push-Location $taskRoot
        try {
            & uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
            if ($LASTEXITCODE -ne 0) { throw 'Physical session checks failed; inspect sanitized receipt.' }
        } finally { Pop-Location }
    } finally {
        $env:JAVA_HOME = $taskSavedJava
        $env:PUB_CACHE = $taskSavedPub
        $env:VPN_I025_LOCAL_ANDROID = $taskSavedOptIn
        $env:VPN_I025_SERIAL = $taskSavedSerial
        $env:VPN_I025_CLIENT_DIR = $taskSavedClient
        $env:PYTEST_ADDOPTS = $taskSavedPytest
    }
    return
}
Copy-Item -LiteralPath (Join-Path $taskRoot 'apps\client\android\app\src\androidTest') -Destination (Join-Path $taskClient 'android\app\src') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'apps\client\android\app\build.gradle.kts') -Destination (Join-Path $taskClient 'android\app\build.gradle.kts') -Force
$taskPreviousJava = $env:JAVA_HOME
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
try {
    Push-Location (Join-Path $taskClient 'android')
    try {
        & "$env:JAVA_HOME\bin\java.exe" -classpath gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --no-daemon '-Ptarget=lib/main.dart' '-Ptarget-platform=android-arm64,android-x64' :app:assembleDebugAndroidTest
        if ($LASTEXITCODE -ne 0) { throw 'I-025 instrumentation compilation failed; phone unchanged.' }
    } finally { Pop-Location }
} finally { $env:JAVA_HOME = $taskPreviousJava }
if ($BuildOnly) { return }
Push-Location $taskRoot
try {
    & uv run --frozen python scripts/check_i025_device.py --adb $taskAdb --serial $Serial --app-apk apps/client/build/app/outputs/flutter-apk/app-debug.apk --test-apk (Join-Path $taskClient 'build\app\outputs\apk\androidTest\debug\app-debug-androidTest.apk')
    if ($LASTEXITCODE -ne 0) { throw 'Physical native checks failed; inspect sanitized i-025-native-android.json.' }
} finally { Pop-Location }
