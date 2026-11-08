[CmdletBinding()]
param(
    [ValidateSet('android/arm64', 'android/arm64,android/amd64')]
    [string]$AndroidPlatforms = 'android/arm64,android/amd64',
    [string]$GoRoot = (Join-Path $PSScriptRoot '..\.cache\toolchains\go1.25.5\go'),
    [string]$GoPath = (Join-Path $PSScriptRoot '..\.cache\toolchains\go1.25.5\gopath'),
    [string]$JdkHome = (Join-Path $PSScriptRoot '..\.cache\toolchains\temurin17.0.21\extract\jdk-17.0.21+9'),
    [string]$AndroidSdk = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$AndroidNdk = (Join-Path $env:LOCALAPPDATA 'Android\Sdk\ndk\28.2.13676358')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourceArchive = Join-Path $repoRoot 'third_party\amnezia-box\amnezia-box-v1.260910.0-src.zip'
$patchFiles = @(
    (Join-Path $repoRoot 'third_party\amnezia-box\peer-stats.patch'),
    (Join-Path $repoRoot 'third_party\amnezia-box\i030-network-monitor-race.patch')
)
$expectedArchiveHash = '2c7563405aa61cd973a7bd43aef0e22e1ed3ee74e6724a18244a641b8bbfe152'
$sourceParent = Join-Path $repoRoot '.cache\upstream\amnezia-box-v1.260910.0-src'
$sourceRoot = Join-Path $sourceParent 'amnezia-box-1.260910.0'
$outputDirectory = Join-Path $repoRoot '.cache\amnezia-box-build'
$aarName = if ($AndroidPlatforms -eq 'android/arm64') {
    'libbox-1.260910-arm64.aar'
} else {
    'libbox-1.260910-arm64-x86_64.aar'
}
$output = Join-Path $outputDirectory $aarName

$requiredPaths = @($sourceArchive) + $patchFiles + @(
    (Join-Path $GoRoot 'bin\go.exe'), (Join-Path $GoPath 'bin\gomobile.exe'),
    (Join-Path $GoPath 'bin\gobind.exe'), (Join-Path $JdkHome 'bin\java.exe'), $AndroidSdk, $AndroidNdk
)
foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required pinned build input is missing: $path" }
}
$actualArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceArchive).Hash.ToLowerInvariant()
if ($actualArchiveHash -ne $expectedArchiveHash) { throw 'Pinned Amnezia Box source archive checksum mismatch.' }

if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'go.mod'))) {
    New-Item -ItemType Directory -Path $sourceParent -Force | Out-Null
    Expand-Archive -LiteralPath $sourceArchive -DestinationPath $sourceParent -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'cmd\internal\build_libbox\main.go'))) {
    throw 'Pinned Amnezia Box source tree is incomplete.'
}
Push-Location $sourceRoot
try {
    foreach ($patchFile in $patchFiles) {
        & git apply --reverse --check $patchFile 2>$null
        if ($LASTEXITCODE -eq 0) { continue }
        & git apply --check $patchFile
        if ($LASTEXITCODE -ne 0) { throw 'Pinned Amnezia Box patch does not apply cleanly.' }
        & git apply $patchFile
        if ($LASTEXITCODE -ne 0) { throw 'Pinned Amnezia Box patch failed to apply.' }
    }
} finally { Pop-Location }
$goVersion = (& (Join-Path $GoRoot 'bin\go.exe') version | Out-String).Trim()
if ($goVersion -ne 'go version go1.25.5 windows/amd64') { throw "Unexpected Go toolchain: $goVersion" }
$javaVersionInfo = New-Object System.Diagnostics.ProcessStartInfo
$javaVersionInfo.FileName = Join-Path $JdkHome 'bin\java.exe'
$javaVersionInfo.Arguments = '-version'
$javaVersionInfo.UseShellExecute = $false
$javaVersionInfo.RedirectStandardError = $true
$javaVersionInfo.CreateNoWindow = $true
$javaVersionProcess = [System.Diagnostics.Process]::Start($javaVersionInfo)
try {
    $javaVersion = $javaVersionProcess.StandardError.ReadToEnd()
    $javaVersionProcess.WaitForExit()
    if ($javaVersionProcess.ExitCode -ne 0) { throw 'OpenJDK version probe failed.' }
} finally { $javaVersionProcess.Dispose() }
if ($javaVersion -notmatch 'version "17\.') { throw 'OpenJDK 17 is required for the Android binding build.' }

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$oldPath = $env:PATH
$oldJavaHome = $env:JAVA_HOME
$oldAndroidHome = $env:ANDROID_HOME
$oldNdkHome = $env:ANDROID_NDK_HOME
$oldGoPath = $env:GOPATH
$oldToolchain = $env:GOTOOLCHAIN
$oldGoWork = $env:GOWORK
try {
    $env:PATH = "$(Join-Path $GoRoot 'bin');$(Join-Path $GoPath 'bin');$(Join-Path $JdkHome 'bin');$oldPath"
    $env:JAVA_HOME = (Resolve-Path $JdkHome).Path
    $env:ANDROID_HOME = (Resolve-Path $AndroidSdk).Path
    $env:ANDROID_NDK_HOME = (Resolve-Path $AndroidNdk).Path
    $env:GOPATH = (Resolve-Path $GoPath).Path
    $env:GOTOOLCHAIN = 'local'
    $env:GOWORK = 'off'
    Push-Location $sourceRoot
    try {
        & (Join-Path $GoRoot 'bin\go.exe') run ./cmd/internal/build_libbox `
            -target android -version 1.260910.0 -tags with_awg,with_gvisor `
            -platform $AndroidPlatforms -output $output -copy=false
        if ($LASTEXITCODE -ne 0) { throw "Amnezia Box Android build failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }
} finally {
    $env:PATH = $oldPath
    $env:JAVA_HOME = $oldJavaHome
    $env:ANDROID_HOME = $oldAndroidHome
    $env:ANDROID_NDK_HOME = $oldNdkHome
    $env:GOPATH = $oldGoPath
    $env:GOTOOLCHAIN = $oldToolchain
    $env:GOWORK = $oldGoWork
}
if (-not (Test-Path -LiteralPath $output)) { throw 'Amnezia Box build did not produce the expected AAR.' }
$outputHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash.ToLowerInvariant()
$abisLabel = if ($AndroidPlatforms -eq 'android/arm64') { 'arm64-v8a' } else { 'arm64-v8a,x86_64' }
Write-Output "libbox_version=1.260910.0; abis=$abisLabel; aar_bytes=$((Get-Item -LiteralPath $output).Length); sha256=$outputHash"
