param(
    [string]$FlutterSdk = "$env:LOCALAPPDATA\ProjectVpn\toolchains\flutter",
    [string]$AndroidSdk = "$env:LOCALAPPDATA\Android\Sdk",
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$ApiOrigin = '',
    [switch]$WindowTunnels,
    [switch]$DeviceChecks
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskClient = Join-Path $taskRoot 'apps\client'
$taskAndroid = Join-Path $taskClient 'android'
$taskPin = Get-Content -LiteralPath (Join-Path $taskClient 'toolchain.json') | ConvertFrom-Json
if (!(Test-Path -LiteralPath (Join-Path $FlutterSdk 'bin\flutter.bat'))) { throw 'Run scripts/install_flutter.ps1 first.' }
if ((& git -C $FlutterSdk rev-parse HEAD).Trim() -ne $taskPin.revision) { throw 'Flutter revision mismatch' }
$taskWrapper = Join-Path $taskRoot 'apps\android\gradle\wrapper\gradle-wrapper.jar'
if ((Get-FileHash -LiteralPath $taskWrapper -Algorithm SHA256).Hash.ToLowerInvariant() -ne '81a82aaea5abcc8ff68b3dfcb58b3c3c429378efd98e7433460610fecd7ae45f') {
    throw 'Gradle wrapper SHA256 mismatch'
}
if (!(Test-Path -LiteralPath (Join-Path $AndroidSdk 'platforms\android-36\android.jar'))) { throw 'Android SDK 36 missing' }
foreach ($taskAbi in @('arm64-v8a', 'x86_64')) {
    if (!(Test-Path -LiteralPath (Join-Path $taskRoot ".cache\i-011\sdk-go-only\jniLibs\$taskAbi\libwg-go.so"))) {
        throw 'Prepare the reviewed I-011 native SDK first; see docs/runbooks/android-poc.md.'
    }
}
# Build an explicit source snapshot outside OneDrive: Dart stat() can reject
# cloud reparse points even when their contents are readable. Never copy private
# configuration, generated build trees or integration credentials into this copy.
$taskBuildRoot = Join-Path $env:LOCALAPPDATA ('ProjectVpn\i013-builds\' + [guid]::NewGuid().ToString('N'))
function Copy-ClientBuildSource([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($taskEntry in Get-ChildItem -LiteralPath $Source -Force) {
        if ($taskEntry.PSIsContainer) {
            if ($taskEntry.Name -notin @('build', '.dart_tool', '.gradle', '.git')) {
                Copy-ClientBuildSource $taskEntry.FullName (Join-Path $Destination $taskEntry.Name)
            }
        } elseif ($taskEntry.Name -notin @('local.properties', 'key.properties', '.metadata', 'GeneratedPluginRegistrant.java') -and
                  $taskEntry.Name -notlike '.flutter-*' -and $taskEntry.Name -notlike '.env*' -and
                  $taskEntry.Extension -notin @('.key', '.pem', '.p12', '.pfx', '.jks', '.keystore')) {
            [IO.File]::WriteAllBytes((Join-Path $Destination $taskEntry.Name), [IO.File]::ReadAllBytes($taskEntry.FullName))
        }
    }
}
Copy-ClientBuildSource $taskClient (Join-Path $taskBuildRoot 'apps\client')
Copy-ClientBuildSource (Join-Path $taskRoot 'apps\android\app\src') (Join-Path $taskBuildRoot 'apps\android\app\src')
New-Item -ItemType Directory -Force -Path (Join-Path $taskBuildRoot 'apps\android\tunnel') | Out-Null
foreach ($taskName in @('build.gradle.kts', 'gradle.lockfile')) {
    Copy-Item -LiteralPath (Join-Path $taskRoot "apps\android\tunnel\$taskName") -Destination (Join-Path $taskBuildRoot "apps\android\tunnel\$taskName")
}
Copy-ClientBuildSource (Join-Path $taskRoot '.cache\i-011\sdk-go-only') (Join-Path $taskBuildRoot '.cache\i-011\sdk-go-only')
$taskSourceClient = $taskClient
$taskClient = Join-Path $taskBuildRoot 'apps\client'
$taskAndroid = Join-Path $taskClient 'android'
Write-Output "Local source snapshot: $taskBuildRoot"
New-Item -ItemType Directory -Force -Path (Join-Path $taskAndroid 'gradle\wrapper') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $taskAndroid 'app\src\main\java\io\flutter\plugins') | Out-Null
Copy-Item -LiteralPath $taskWrapper -Destination (Join-Path $taskAndroid 'gradle\wrapper\gradle-wrapper.jar')
Copy-Item -LiteralPath (Join-Path $taskClient 'gradle-wrapper.properties') -Destination (Join-Path $taskAndroid 'gradle\wrapper\gradle-wrapper.properties')
[IO.File]::WriteAllText((Join-Path $taskAndroid 'gradlew.bat'), '@echo off' + "`r`n" +
    '"%JAVA_HOME%\bin\java.exe" -classpath "%~dp0gradle\wrapper\gradle-wrapper.jar" org.gradle.wrapper.GradleWrapperMain %*' + "`r`n", [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $taskAndroid 'local.properties'),
    ('sdk.dir=' + $AndroidSdk.Replace('\', '/').Replace(':', '\:') + "`nflutter.sdk=" +
    $FlutterSdk.Replace('\', '/').Replace(':', '\:') + "`n"), [Text.UTF8Encoding]::new($false))
$taskPreviousJava = $env:JAVA_HOME
$taskPreviousPub = $env:PUB_CACHE
$env:JAVA_HOME = $JavaHome
$env:PUB_CACHE = Join-Path (Split-Path -Parent $FlutterSdk) 'pub-cache'
$taskFlutter = Join-Path $FlutterSdk 'bin\flutter.bat'
try {
    Push-Location -LiteralPath $taskClient
    try {
        & $taskFlutter pub get --enforce-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Locked pub get failed' }
        & (Join-Path $FlutterSdk 'bin\dart.bat') format --output=none --set-exit-if-changed lib test integration_test
        if ($LASTEXITCODE -ne 0) { throw 'Dart formatting failed' }
        & $taskFlutter analyze --no-pub
        if ($LASTEXITCODE -ne 0) { throw 'Dart analysis failed' }
        & $taskFlutter test --no-pub
        if ($LASTEXITCODE -ne 0) { throw 'Flutter tests failed' }
    } finally { Pop-Location }
    Push-Location -LiteralPath $taskClient
    try {
        $taskArguments = @('build', 'apk', '--debug', '--no-pub', '--target', 'lib/main.dart', '--target-platform', 'android-arm64,android-x64')
        if ($ApiOrigin) { $taskArguments += "--dart-define=VPN_API_ORIGIN=$ApiOrigin" }
        if ($WindowTunnels) { $taskArguments += "--dart-define=VPN_WINDOW_TUNNELS=true" }
        & $taskFlutter @taskArguments
        if ($LASTEXITCODE -ne 0) { throw 'Flutter Android build failed' }
        # Flutter CLI rewrites Windows paths without escaping drive colons;
        # normalize its generated file before Android lint (do not suppress lint).
        [IO.File]::WriteAllText((Join-Path $taskAndroid 'local.properties'),
            ('sdk.dir=' + $AndroidSdk.Replace('\', '/').Replace(':', '\:') + "`nflutter.sdk=" +
            $FlutterSdk.Replace('\', '/').Replace(':', '\:') + "`n"), [Text.UTF8Encoding]::new($false))
        Push-Location -LiteralPath $taskAndroid
        try {
            & (Join-Path $JavaHome 'bin\java.exe') '-classpath' (Join-Path $taskAndroid 'gradle\wrapper\gradle-wrapper.jar') org.gradle.wrapper.GradleWrapperMain `
                '--no-daemon' '-Ptarget=lib/main.dart' '-Ptarget-platform=android-arm64,android-x64' ':app:testDebugUnitTest' ':app:lintDebug'
            if ($LASTEXITCODE -ne 0) { throw 'Android tests/lint failed' }
        } finally { Pop-Location }
        # Flutter regenerates the native plugin registrant for each build mode.
        # Direct Gradle release after debug retains the integration_test reference
        # while release excludes that dev dependency, causing javac to fail.
        # In the pinned Flutter SDK, --no-pub also skips plugin regeneration.
        # Allow pub here after the enforced-lockfile check above so release can
        # replace the debug registrant and omit dev-only native plugins.
        $taskReleaseArguments = @('build', 'apk', '--release', '--target', 'lib/main.dart', '--target-platform', 'android-arm64,android-x64')
        if ($ApiOrigin) { $taskReleaseArguments += "--dart-define=VPN_API_ORIGIN=$ApiOrigin" }
        $taskPubLockHash = (Get-FileHash -LiteralPath (Join-Path $taskClient 'pubspec.lock') -Algorithm SHA256).Hash
        if ($WindowTunnels) { $taskReleaseArguments += "--dart-define=VPN_WINDOW_TUNNELS=true" }
        & $taskFlutter @taskReleaseArguments
        if ($LASTEXITCODE -ne 0) { throw 'Flutter Android release build failed' }
        if ((Get-FileHash -LiteralPath (Join-Path $taskClient 'pubspec.lock') -Algorithm SHA256).Hash -ne $taskPubLockHash) {
            throw 'Release build changed the enforced pub lockfile; artifacts will not be exported'
        }
        # Export ordinary main.dart artifacts before any optional test target.
        foreach ($taskOutput in @('outputs', 'reports', 'test-results')) {
            $taskFrom = Join-Path $taskClient "build\app\$taskOutput"
            if (Test-Path -LiteralPath $taskFrom) {
                $taskTo = Join-Path $taskSourceClient 'build\app'
                New-Item -ItemType Directory -Force -Path $taskTo | Out-Null
                Copy-Item -LiteralPath $taskFrom -Destination $taskTo -Recurse -Force
            }
        }
        if ($DeviceChecks) {
            $taskDevices = @(& (Join-Path $AndroidSdk 'platform-tools\adb.exe') devices | Where-Object { $_ -match '\tdevice\s*$' })
            if ($taskDevices.Count -ne 1) { throw 'Connect/unlock exactly one physical Android phone.' }
            $taskSerial = ($taskDevices[0] -split '\s+')[0]
            & $taskFlutter test integration_test/native_bridge_test.dart -d $taskSerial --no-pub
            if ($LASTEXITCODE -ne 0) { throw 'Physical native bridge test failed' }
        }
    } finally { Pop-Location }
} finally { $env:JAVA_HOME = $taskPreviousJava; $env:PUB_CACHE = $taskPreviousPub }
