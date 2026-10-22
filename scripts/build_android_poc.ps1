param(
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr',
    [string]$AndroidSdk = "$env:LOCALAPPDATA\Android\Sdk"
)
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$androidPath = Join-Path $repoPath 'apps\android'
$wrapperPath = Join-Path $androidPath 'gradle\wrapper\gradle-wrapper.jar'
if ((Get-FileHash -LiteralPath $wrapperPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne '81a82aaea5abcc8ff68b3dfcb58b3c3c429378efd98e7433460610fecd7ae45f') {
    throw 'Gradle wrapper hash mismatch'
}
if (!(Test-Path -LiteralPath (Join-Path $JavaHome 'bin\java.exe'))) { throw 'JDK missing' }
if (!(Test-Path -LiteralPath (Join-Path $AndroidSdk 'platforms\android-36\android.jar'))) { throw 'Android platform 36 missing' }
foreach ($abi in @('arm64-v8a', 'x86_64')) {
    if (!(Test-Path -LiteralPath (Join-Path $repoPath ".cache\i-011\sdk-go-only\jniLibs\$abi\libwg-go.so"))) { throw "Native library missing: $abi" }
}
$sdkProperty = $AndroidSdk.Replace('\', '/').Replace(':', '\:')
[IO.File]::WriteAllText((Join-Path $androidPath 'local.properties'), "sdk.dir=$sdkProperty`n", [Text.UTF8Encoding]::new($false))
Push-Location -LiteralPath $androidPath
try {
    & (Join-Path $JavaHome 'bin\java.exe') '-classpath' $wrapperPath 'org.gradle.wrapper.GradleWrapperMain' `
        '--no-daemon' '--dependency-verification=strict' ':app:testDebugUnitTest' ':app:lintDebug' `
        ':app:assembleDebug' ':app:assembleDebugAndroidTest' ':app:assembleRelease' ':tunnel:assembleRelease'
    if ($LASTEXITCODE -ne 0) { throw "Android build failed: $LASTEXITCODE" }
} finally { Pop-Location }
